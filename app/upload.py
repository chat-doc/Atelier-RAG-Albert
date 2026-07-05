"""Uploader Albert : lit segments.jsonl et pousse dans une collection.

Chaine :
  1. (optionnel) vider la collection cible
  2. Lire segments.jsonl produit par app.cli
  3. Grouper par document_id (les lignes 'document' marquent le debut,
     les lignes 'segment' contiennent les chunks)
  4. Pour chaque document :
     - Filtrer les exclus (excluded=true)
     - Creer une coquille Albert (POST /v1/documents)
     - Envoyer les chunks par lots de 64 (POST /v1/documents/{id}/chunks)
  5. Ecrire upload_report.json avec stats + erreurs

Aucune resiliance sur les collections deja pleines (quota 10 Mo) : on
laisse Albert renvoyer 400 et on log l'erreur, l'utilisateur voit dans
le rapport et decide.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .albert_client import AlbertClient, AlbertError, CHUNKS_MAX_PER_REQUEST


logger = logging.getLogger("segmenteur.upload")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Streaming reader tolerant aux lignes corrompues."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def group_by_document(rows: Iterable[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Regroupe les lignes JSONL par document.

    Le format produit par app.cli alterne :
      {"kind": "document", ...}
      {"kind": "segment", ...}
      {"kind": "segment", ...}
      {"kind": "document", ...}
      ...

    Cette fonction yield (document_dict, [segments]) pour chaque groupe.
    """
    current_doc: Optional[dict[str, Any]] = None
    current_segments: list[dict[str, Any]] = []

    for row in rows:
        kind = row.get("kind")
        if kind == "document":
            if current_doc is not None:
                yield current_doc, current_segments
            current_doc = row
            current_segments = []
        elif kind == "segment" and current_doc is not None:
            current_segments.append(row)

    if current_doc is not None:
        yield current_doc, current_segments


def batch(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def upload_segments(
    segments_path: Path,
    report_path: Path,
    collection_id: str,
    api_key: str,
    base_url: str,
    empty_first: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Uploade les segments vers Albert et retourne un rapport."""
    started = time.monotonic()

    if not segments_path.is_file():
        raise FileNotFoundError(f"segments.jsonl introuvable : {segments_path}")

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "collection_id": collection_id,
        "params": {
            "empty_first": empty_first,
            "dry_run": dry_run,
            "chunks_batch_max": CHUNKS_MAX_PER_REQUEST,
        },
        "stats": {
            "documents_seen": 0,
            "documents_excluded": 0,
            "documents_uploaded": 0,
            "chunks_uploaded": 0,
            "batches_sent": 0,
            "errors": 0,
        },
        "empty_before": None,
        "uploaded": [],
        "errors": [],
    }

    if dry_run:
        logger.info("DRY-RUN active : aucun appel Albert. On simule.")
        for doc, segments in group_by_document(read_jsonl(segments_path)):
            report["stats"]["documents_seen"] += 1
            if doc.get("excluded"):
                report["stats"]["documents_excluded"] += 1
                continue
            report["stats"]["documents_uploaded"] += 1
            report["stats"]["chunks_uploaded"] += len(segments)
            n_batches = (len(segments) + CHUNKS_MAX_PER_REQUEST - 1) // CHUNKS_MAX_PER_REQUEST
            report["stats"]["batches_sent"] += n_batches
            report["uploaded"].append({
                "rel_path": doc.get("rel_path"),
                "title": doc.get("title"),
                "segments_count": len(segments),
                "batches": n_batches,
                "document_id_simulated": doc.get("document_id"),
            })
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        _write_report(report_path, report)
        return report

    with AlbertClient(api_key=api_key, base_url=base_url) as client:

        if empty_first:
            logger.info("Vidage prealable de la collection %s...", collection_id)
            emptied = client.empty_collection(collection_id)
            report["empty_before"] = emptied
            logger.info("Vide : %d documents supprimes, %d erreurs.", emptied["deleted"], len(emptied["errors"]))

        for doc, segments in group_by_document(read_jsonl(segments_path)):
            report["stats"]["documents_seen"] += 1
            rel_path = str(doc.get("rel_path") or "?")

            if doc.get("excluded"):
                report["stats"]["documents_excluded"] += 1
                continue

            if not segments:
                logger.warning("%s : aucun segment, skip.", rel_path)
                continue

            title = str(doc.get("title") or "Document")
            metadata = {
                "source_type": "markdown",
                "source_url": str(doc.get("source_url") or ""),
                "rel_path": rel_path,
                "document_id_source": str(doc.get("document_id") or ""),
            }

            try:
                albert_doc_id = client.create_document(collection_id, title, metadata)
            except AlbertError as exc:
                logger.error("%s : createDocument echoue : %s", rel_path, exc)
                report["stats"]["errors"] += 1
                report["errors"].append({
                    "rel_path": rel_path,
                    "phase": "create_document",
                    "message": str(exc),
                })
                continue

            uploaded_chunks = 0
            batches = 0
            try:
                for chunk_batch in batch(segments, CHUNKS_MAX_PER_REQUEST):
                    payload = [
                        {
                            "content": s["content"],
                            "metadata": s.get("metadata", {}),
                        }
                        for s in chunk_batch
                    ]
                    client.append_chunks(albert_doc_id, payload)
                    uploaded_chunks += len(chunk_batch)
                    batches += 1
                report["stats"]["documents_uploaded"] += 1
                report["stats"]["chunks_uploaded"] += uploaded_chunks
                report["stats"]["batches_sent"] += batches
                report["uploaded"].append({
                    "rel_path": rel_path,
                    "title": title,
                    "albert_document_id": albert_doc_id,
                    "chunks_uploaded": uploaded_chunks,
                    "batches": batches,
                })
                logger.info("%s : OK (doc %s, %d chunks / %d batches)", rel_path, albert_doc_id, uploaded_chunks, batches)
            except AlbertError as exc:
                logger.error("%s : appendChunks echoue apres %d chunks : %s", rel_path, uploaded_chunks, exc)
                report["stats"]["errors"] += 1
                report["errors"].append({
                    "rel_path": rel_path,
                    "phase": "append_chunks",
                    "albert_document_id": albert_doc_id,
                    "chunks_uploaded_before_error": uploaded_chunks,
                    "message": str(exc),
                })

    report["duration_seconds"] = round(time.monotonic() - started, 2)
    _write_report(report_path, report)
    return report


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="upload",
        description="Uploade segments.jsonl vers une collection Albert.",
    )
    parser.add_argument("--segments", type=Path, required=True, help="Chemin vers segments.jsonl.")
    parser.add_argument("--report", type=Path, default=Path("upload_report.json"))
    parser.add_argument("--collection-id", required=True, help="ID de collection Albert cible.")
    parser.add_argument("--api-key", required=True, help="Cle Albert (secret CI).")
    parser.add_argument(
        "--base-url",
        default="https://albert.api.etalab.gouv.fr",
        help="Base URL de l'API Albert.",
    )
    parser.add_argument("--empty-first", action="store_true", help="Vide la collection avant l'upload.")
    parser.add_argument("--dry-run", action="store_true", help="Simule l'upload sans appeler Albert.")
    args = parser.parse_args(argv)

    try:
        report = upload_segments(
            segments_path=args.segments,
            report_path=args.report,
            collection_id=args.collection_id,
            api_key=args.api_key,
            base_url=args.base_url,
            empty_first=args.empty_first,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erreur upload")
        return 1

    stats = report["stats"]
    logger.info(
        "Termine en %.2fs : %d documents (%d exclus, %d uploaded), %d chunks, %d erreurs.",
        report["duration_seconds"],
        stats["documents_seen"],
        stats["documents_excluded"],
        stats["documents_uploaded"],
        stats["chunks_uploaded"],
        stats["errors"],
    )
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

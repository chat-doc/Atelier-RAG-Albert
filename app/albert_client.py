"""Client HTTP minimaliste pour l'API Albert (Etalab).

Endpoints utilises :
- POST /v1/documents          creer un document (avec ou sans fichier)
- POST /v1/documents/{id}/chunks  ajouter des chunks (max 64 par requete)
- GET  /v1/collections/{id}/documents  lister documents (pagine)
- DELETE /v1/documents/{id}    supprimer un document
- GET  /v1/collections/{id}    metadata de collection (size, count)

Retry automatique sur 429 (rate limit) avec backoff exponentiel.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx


logger = logging.getLogger("segmenteur.albert")

DEFAULT_BASE_URL = "https://albert.api.etalab.gouv.fr"
CHUNKS_MAX_PER_REQUEST = 64


class AlbertError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class AlbertClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        max_retries: int = 4,
    ):
        if not api_key:
            raise ValueError("api_key est obligatoire.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "atelier-rag-albert/0.1",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AlbertClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------

    def get_collection(self, collection_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/collections/{collection_id}")

    def list_collection_documents(
        self, collection_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retourne tous les documents d'une collection (avec pagination)."""
        documents: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._request(
                "GET",
                f"/v1/collections/{collection_id}/documents",
                params={"limit": limit, "offset": offset},
            )
            page = data.get("data") or data.get("documents") or []
            if not isinstance(page, list) or not page:
                break
            documents.extend(page)
            if len(page) < limit:
                break
            offset += len(page)
        return documents

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    def create_document(
        self,
        collection_id: str,
        name: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Cree une coquille de document (sans fichier). Retourne l'id."""
        fields: dict[str, Any] = {
            "collection_id": collection_id,
            "name": name,
        }
        if metadata:
            fields["metadata"] = json.dumps(
                metadata, ensure_ascii=False, separators=(",", ":")
            )
        response = self._request("POST", "/v1/documents", data=fields)
        doc_id = self._extract_document_id(response)
        if not doc_id:
            raise AlbertError(f"createDocument sans id : {response}")
        return doc_id

    def append_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
        """Ajoute des chunks a un document existant (max 64 par requete)."""
        if not chunks:
            return {}
        if len(chunks) > CHUNKS_MAX_PER_REQUEST:
            raise AlbertError(
                f"Trop de chunks ({len(chunks)}) : max {CHUNKS_MAX_PER_REQUEST} par requete."
            )
        return self._request(
            "POST",
            f"/v1/documents/{document_id}/chunks",
            json={"chunks": chunks},
        )

    def delete_document(self, document_id: str) -> None:
        self._request("DELETE", f"/v1/documents/{document_id}", expect_json=False)

    def empty_collection(self, collection_id: str) -> dict[str, Any]:
        """Supprime tous les documents d'une collection. Retourne un compte."""
        docs = self.list_collection_documents(collection_id)
        deleted = 0
        errors: list[dict[str, Any]] = []
        for doc in docs:
            doc_id = str(doc.get("id") or "")
            if not doc_id:
                continue
            try:
                self.delete_document(doc_id)
                deleted += 1
            except AlbertError as exc:
                errors.append({"id": doc_id, "error": str(exc)})
        return {"deleted": deleted, "errors": errors, "total": len(docs)}

    # ------------------------------------------------------------------
    # Helpers HTTP
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        url = self.base_url + path
        delay = 1.0
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                response = self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("Erreur reseau %s : %s", method, exc)
                if attempt > self.max_retries:
                    raise AlbertError(f"Erreur reseau : {exc}") from exc
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else delay
                logger.warning(
                    "429 rate limit, retry dans %.1fs (tentative %d/%d)",
                    sleep_for,
                    attempt,
                    self.max_retries + 1,
                )
                time.sleep(sleep_for)
                delay = min(delay * 2, 30)
                continue

            if response.status_code >= 500 and attempt <= self.max_retries:
                logger.warning(
                    "%d serveur, retry dans %.1fs (tentative %d/%d)",
                    response.status_code,
                    delay,
                    attempt,
                    self.max_retries + 1,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            if response.status_code >= 400:
                body = response.text[:500]
                raise AlbertError(
                    f"HTTP {response.status_code} : {body}",
                    status=response.status_code,
                    body=body,
                )

            if not expect_json:
                return {}
            if not response.text:
                return {}
            try:
                return response.json()
            except json.JSONDecodeError:
                return {"raw": response.text}

        raise AlbertError(f"Echec apres {self.max_retries} tentatives : {last_exc}")

    @staticmethod
    def _extract_document_id(response: dict[str, Any]) -> Optional[str]:
        for path in (["id"], ["data", "id"], ["document", "id"]):
            cursor: Any = response
            found = True
            for segment in path:
                if not isinstance(cursor, dict) or segment not in cursor:
                    found = False
                    break
                cursor = cursor[segment]
            if found and isinstance(cursor, (str, int)):
                return str(cursor)
        return None

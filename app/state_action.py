"""Script CLI appele par le CI pour agir sur Albert et produire un rapport.

Responsabilites :
  1. Optionnellement executer une action Albert (create/rename/empty/delete).
  2. Toujours fetcher l'etat courant (me, collections, models).
  3. Generer report.json (donnees brutes) + report.html (rapport formate).
  4. Ecrire un Job Summary Markdown si $GITHUB_STEP_SUMMARY est defini
     (compatible GitHub Actions).

Aucun commit dans le repo : la sortie est purement des artefacts CI. Ces
artefacts sont visibles uniquement aux membres du projet (repo prive).
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .albert_client import AlbertClient, AlbertError


logger = logging.getLogger("atelier.state")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ACTIONS = ("refresh", "create", "rename", "empty", "delete")

QUOTA_BYTES = 10 * 1000 * 1000


def do_action(client: AlbertClient, args: argparse.Namespace) -> dict[str, Any]:
    """Execute l'action demandee. Retourne un dict "result" pour le rapport."""
    action = args.action
    if action == "refresh":
        return {"performed": False, "action": "refresh"}

    if action == "create":
        if not args.name:
            raise ValueError("--name obligatoire pour create")
        payload: dict[str, Any] = {"name": args.name}
        if args.description:
            payload["description"] = args.description
        if args.visibility:
            payload["visibility"] = args.visibility
        resp = client._request("POST", "/v1/collections", json=payload)
        return {"performed": True, "action": "create", "created_id": resp.get("id"), "name": args.name}

    if action == "rename":
        if not args.collection_id:
            raise ValueError("--collection-id obligatoire pour rename")
        fields: dict[str, Any] = {}
        if args.name:
            fields["name"] = args.name
        if args.description:
            fields["description"] = args.description
        if args.visibility:
            fields["visibility"] = args.visibility
        client._request("PATCH", f"/v1/collections/{args.collection_id}", json=fields, expect_json=False)
        return {"performed": True, "action": "rename", "renamed_id": args.collection_id, "fields": list(fields.keys())}

    if action == "empty":
        if not args.collection_id:
            raise ValueError("--collection-id obligatoire pour empty")
        result = client.empty_collection(args.collection_id)
        return {"performed": True, "action": "empty", "emptied_id": args.collection_id, **result}

    if action == "delete":
        if not args.collection_id:
            raise ValueError("--collection-id obligatoire pour delete")
        client._request("DELETE", f"/v1/collections/{args.collection_id}", expect_json=False)
        return {"performed": True, "action": "delete", "deleted_id": args.collection_id}

    raise ValueError(f"Action inconnue : {action}")


def _list_collections(client: AlbertClient) -> tuple[list[dict[str, Any]], list[str]]:
    """Essaie plusieurs variantes de l'endpoint collections.

    Historiquement `/v1/collections` fonctionne, mais on a observe un 404
    intermittent. On tente avec params, sans params, trailing slash, puis
    `/v1/me/collections` en dernier recours. Retourne (collections, tentatives).
    """
    attempts: list[str] = []
    variants: list[tuple[str, dict[str, Any] | None]] = [
        ("/v1/collections", {"limit": 100, "order_by": "id", "order_direction": "asc"}),
        ("/v1/collections", {"limit": 100}),
        ("/v1/collections", None),
        ("/v1/collections/", None),
        ("/v1/me/collections", None),
    ]
    for path, params in variants:
        label = path + (f"?{'&'.join(f'{k}={v}' for k,v in params.items())}" if params else "")
        try:
            resp = client._request("GET", path, params=params)
        except AlbertError as exc:
            attempts.append(f"{label} -> {exc}")
            continue
        data = resp.get("data") or resp.get("collections") or []
        if isinstance(data, list):
            attempts.append(f"{label} -> OK ({len(data)})")
            return data, attempts
        attempts.append(f"{label} -> forme inattendue")
    return [], attempts


def fetch_state(client: AlbertClient) -> dict[str, Any]:
    """Fetch me + collections + models. Retourne les 3 payloads."""
    state: dict[str, Any] = {"errors": {}}
    try:
        state["me"] = client._request("GET", "/v1/me/info")
    except AlbertError as exc:
        state["me"] = None
        state["errors"]["me"] = str(exc)

    collections, attempts = _list_collections(client)
    state["collections"] = collections
    if not collections and attempts and all("OK" not in a for a in attempts):
        state["errors"]["collections"] = " | ".join(attempts)

    try:
        resp = client._request("GET", "/v1/models")
        state["models"] = resp.get("data") or []
    except AlbertError as exc:
        state["models"] = []
        state["errors"]["models"] = str(exc)
    return state


def human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.2f} {unit}" if n < 10 else f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def fmt_ts(v: Any) -> str:
    if v in (None, "", 0):
        return "-"
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(v)))
    return str(v)


def quota_pct(size: int) -> int:
    if QUOTA_BYTES <= 0:
        return 0
    return min(100, round(size * 100 / QUOTA_BYTES))


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_markdown(state: dict[str, Any], action_result: dict[str, Any]) -> str:
    """Rapport Markdown (pour GitHub Job Summary + logs)."""
    lines: list[str] = []
    lines.append(f"# Atelier RAG Albert — {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
    lines.append("")
    action = action_result.get("action", "?")
    if action_result.get("performed"):
        lines.append(f"**Action executée** : `{action}`")
        for k, v in action_result.items():
            if k in ("performed", "action"):
                continue
            lines.append(f"- `{k}` : {v}")
    else:
        lines.append("**Action executée** : `refresh` (aucune modification)")
    lines.append("")

    me = state.get("me") or {}
    if me:
        lines.append("## Compte Albert")
        lines.append("")
        lines.append(f"- E-mail : {me.get('email', '-')}")
        lines.append(f"- Identifiant : {me.get('id', '-')}")
        lines.append(f"- Organisation : {me.get('organization', '-')}")
        lines.append(f"- Priorite : {me.get('priority', '-')}")
        lines.append(f"- Expire : {fmt_ts(me.get('expires'))}")
        perms = me.get("permissions") or []
        lines.append(f"- Permissions : {', '.join(perms) if perms else '(aucune)'}")
        lines.append("")

    collections = state.get("collections") or []
    my_email = (me.get("email") or "").lower() if me else ""
    mine = [c for c in collections if (c.get("owner") or "").lower() == my_email]
    others = [c for c in collections if (c.get("owner") or "").lower() != my_email]

    lines.append(f"## Mes collections ({len(mine)})")
    lines.append("")
    if mine:
        lines.append("| ID | Nom | Docs | Taille | Quota | Visibilite | Créée |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for c in mine:
            size = int(c.get("size", 0) or 0)
            pct = quota_pct(size)
            pct_str = f"{pct}%"
            if pct >= 95:
                pct_str = f"⚠️ **{pct}%**"
            elif pct >= 80:
                pct_str = f"⚠ {pct}%"
            lines.append(
                f"| `{c.get('id')}` | {c.get('name', '-')} | {c.get('documents', 0)} | "
                f"{human_bytes(size)} | {pct_str} | {c.get('visibility', '-')} | {fmt_ts(c.get('created'))} |"
            )
    else:
        lines.append("_Aucune collection ne t'appartient._")
    lines.append("")

    if others:
        lines.append(f"<details><summary>Collections publiques d'autres utilisateurs ({len(others)})</summary>")
        lines.append("")
        lines.append("| ID | Nom | Docs | Taille | Propriétaire |")
        lines.append("|---|---|---:|---:|---|")
        for c in others[:50]:  # cap pour ne pas exploser le summary
            lines.append(
                f"| `{c.get('id')}` | {c.get('name', '-')} | {c.get('documents', 0)} | "
                f"{human_bytes(int(c.get('size', 0) or 0))} | {c.get('owner', '-')} |"
            )
        if len(others) > 50:
            lines.append(f"| ... | _{len(others) - 50} de plus_ | | | |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    models = state.get("models") or []
    if models:
        lines.append(f"## Modèles disponibles ({len(models)})")
        lines.append("")
        lines.append("| Identifiant | Type | Aliases | Contexte |")
        lines.append("|---|---|---|---:|")
        for m in models:
            aliases = ", ".join(m.get("aliases") or [])
            lines.append(
                f"| `{m.get('id', '-')}` | {m.get('type', '-')} | {aliases} | {m.get('max_context_length', '-')} |"
            )
        lines.append("")

    errors = state.get("errors") or {}
    if errors:
        lines.append("## Erreurs")
        lines.append("")
        for k, v in errors.items():
            lines.append(f"- **{k}** : {v}")
        lines.append("")

    return "\n".join(lines)


def render_html(state: dict[str, Any], action_result: dict[str, Any]) -> str:
    """Rapport HTML browsable (pour GitLab expose_as ou download GitHub)."""

    def esc(v: Any) -> str:
        return html.escape(str(v if v is not None else "-"))

    me = state.get("me") or {}
    my_email = (me.get("email") or "").lower()
    collections = state.get("collections") or []
    mine = [c for c in collections if (c.get("owner") or "").lower() == my_email]
    others = [c for c in collections if (c.get("owner") or "").lower() != my_email]
    models = state.get("models") or []
    errors = state.get("errors") or {}

    def row_collection(c: dict[str, Any], include_owner: bool = False) -> str:
        size = int(c.get("size", 0) or 0)
        pct = quota_pct(size)
        cls = "ok" if pct < 80 else ("warn" if pct < 95 else "full")
        owner_cell = f"<td class='mono small'>{esc(c.get('owner', '-'))}</td>" if include_owner else ""
        return (
            f"<tr>"
            f"<td class='mono'>{esc(c.get('id'))}</td>"
            f"<td>{esc(c.get('name', '-'))}</td>"
            f"<td class='right'>{esc(c.get('documents', 0))}</td>"
            f"<td class='right'>{esc(human_bytes(size))}</td>"
            f"<td class='right'><span class='pct {cls}'>{pct}%</span></td>"
            f"<td>{esc(c.get('visibility', '-'))}</td>"
            f"<td>{esc(fmt_ts(c.get('created')))}</td>"
            f"{owner_cell}"
            f"</tr>"
        )

    def row_model(m: dict[str, Any]) -> str:
        aliases = ", ".join(m.get("aliases") or [])
        costs = m.get("costs") or {}
        return (
            f"<tr>"
            f"<td class='mono small'>{esc(m.get('id'))}</td>"
            f"<td class='small'>{esc(m.get('type'))}</td>"
            f"<td class='small'>{esc(aliases)}</td>"
            f"<td class='right mono small'>{esc(m.get('max_context_length'))}</td>"
            f"<td class='right mono small'>{esc(costs.get('prompt_tokens'))}</td>"
            f"<td class='right mono small'>{esc(costs.get('completion_tokens'))}</td>"
            f"</tr>"
        )

    action = action_result.get("action", "?")
    if action_result.get("performed"):
        action_bits = "".join(
            f"<li><code>{esc(k)}</code> : {esc(v)}</li>"
            for k, v in action_result.items()
            if k not in ("performed", "action")
        )
        action_html = (
            f"<div class='action-box performed'>"
            f"<strong>Action executée</strong> : <code>{esc(action)}</code>"
            f"<ul>{action_bits}</ul>"
            f"</div>"
        )
    else:
        action_html = f"<div class='action-box'><strong>Action</strong> : <code>refresh</code> (aucune modification)</div>"

    my_html = (
        "".join(row_collection(c) for c in mine)
        if mine
        else "<tr><td colspan='7' class='muted small'>Aucune collection ne t'appartient.</td></tr>"
    )
    other_html = (
        "".join(row_collection(c, include_owner=True) for c in others[:100])
        if others
        else "<tr><td colspan='7' class='muted small'>-</td></tr>"
    )
    models_html = (
        "".join(row_model(m) for m in models)
        if models
        else "<tr><td colspan='6' class='muted small'>-</td></tr>"
    )
    errors_html = ""
    if errors:
        rows = "".join(f"<tr><td><strong>{esc(k)}</strong></td><td>{esc(v)}</td></tr>" for k, v in errors.items())
        errors_html = f"<h2>Erreurs</h2><table class='data-table'><tbody>{rows}</tbody></table>"

    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Atelier RAG Albert — rapport</title>
<style>
  :root {{
    --bg:#f5f6f8; --panel:#fff; --panel-alt:#fafbfc; --border:#dde1e6;
    --text:#1a1d21; --muted:#6b7280; --accent:#7cb518; --accent-soft:#eaf5d4;
    --error:#dc2626; --error-soft:#fee2e2; --warn:#eab308;
  }}
  html,body {{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:ui-monospace,Consolas,monospace;font-size:14px;line-height:1.55;}}
  main {{max-width:1200px;margin:0 auto;padding:1.5rem;}}
  h1 {{font-size:1.3rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.3rem;}}
  h2 {{font-size:1rem;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--border);padding-bottom:.5rem;margin-top:2rem;}}
  .meta-time {{color:var(--muted);font-size:.85rem;margin-bottom:1rem;}}
  .action-box {{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--muted);border-radius:4px;padding:.9rem 1.1rem;margin-bottom:1.2rem;}}
  .action-box.performed {{border-left-color:var(--accent);}}
  .action-box ul {{margin:.4rem 0 0;padding-left:1.4rem;}}
  .data-table {{width:100%;border-collapse:separate;border-spacing:0;background:var(--panel);border:1px solid var(--border);border-radius:4px;margin-bottom:1rem;}}
  .data-table th,.data-table td {{padding:.6rem .85rem;text-align:left;border-bottom:1px solid var(--border);font-size:.82rem;vertical-align:middle;}}
  .data-table th {{background:var(--panel-alt);color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:.68rem;font-weight:700;border-bottom:1px solid #c0c7d0;}}
  .data-table tbody tr:last-child td {{border-bottom:none;}}
  .data-table tbody tr:hover {{background:var(--panel-alt);}}
  .mono {{font-family:ui-monospace,Consolas,monospace;}}
  .muted {{color:var(--muted);}}
  .small {{font-size:.78rem;}}
  .right {{text-align:right;}}
  .pct {{display:inline-block;padding:.05rem .45rem;border-radius:3px;font-size:.72rem;font-weight:700;background:var(--accent-soft);color:var(--accent);}}
  .pct.warn {{background:#fef9c3;color:#a16207;}}
  .pct.full {{background:var(--error-soft);color:var(--error);}}
  .me-grid {{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem 1.5rem;background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:1rem 1.2rem;margin-bottom:1rem;}}
  .me-grid dt {{color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.05em;font-weight:600;margin-bottom:.1rem;}}
  .me-grid dd {{margin:0;font-family:ui-monospace,Consolas,monospace;font-size:.85rem;}}
</style>
</head>
<body>
<main>
  <h1>Atelier RAG Albert</h1>
  <p class="meta-time">Rapport généré le {now}</p>
  {action_html}

  <h2>Compte Albert</h2>
  <dl class="me-grid">
    <div><dt>E-mail</dt><dd>{esc(me.get('email'))}</dd></div>
    <div><dt>Nom</dt><dd>{esc(me.get('name'))}</dd></div>
    <div><dt>Identifiant</dt><dd>{esc(me.get('id'))}</dd></div>
    <div><dt>Organisation</dt><dd>{esc(me.get('organization'))}</dd></div>
    <div><dt>Priorité</dt><dd>{esc(me.get('priority'))}</dd></div>
    <div><dt>Expire</dt><dd>{esc(fmt_ts(me.get('expires')))}</dd></div>
    <div><dt>Permissions</dt><dd>{esc(', '.join(me.get('permissions') or []) or '(aucune)')}</dd></div>
  </dl>

  <h2>Mes collections ({len(mine)})</h2>
  <table class="data-table">
    <thead>
      <tr><th>ID</th><th>Nom</th><th class="right">Docs</th><th class="right">Taille</th><th class="right">Quota</th><th>Visibilité</th><th>Créée</th></tr>
    </thead>
    <tbody>{my_html}</tbody>
  </table>

  <h2>Collections publiques d'autres ({len(others)})</h2>
  <table class="data-table">
    <thead>
      <tr><th>ID</th><th>Nom</th><th class="right">Docs</th><th class="right">Taille</th><th class="right">Quota</th><th>Visibilité</th><th>Créée</th><th>Propriétaire</th></tr>
    </thead>
    <tbody>{other_html}</tbody>
  </table>

  <h2>Modèles disponibles ({len(models)})</h2>
  <table class="data-table">
    <thead>
      <tr><th>Identifiant</th><th>Type</th><th>Aliases</th><th class="right">Contexte</th><th class="right">Coût prompt</th><th class="right">Coût completion</th></tr>
    </thead>
    <tbody>{models_html}</tbody>
  </table>

  {errors_html}
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="state_action")
    parser.add_argument("--action", default="refresh", choices=ACTIONS)
    parser.add_argument("--collection-id", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--visibility", default="private", choices=["private", "public"])
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("public"),
                        help="Dossier pour report.html + report.json.")
    args = parser.parse_args()

    api_key = args.api_key.strip()
    if not api_key:
        logger.error("Cle Albert vide.")
        return 1

    started = time.monotonic()
    action_result: dict[str, Any] = {}
    state: dict[str, Any] = {}

    try:
        with AlbertClient(api_key) as client:
            try:
                action_result = do_action(client, args)
            except (AlbertError, ValueError) as exc:
                logger.error("Action %s KO : %s", args.action, exc)
                action_result = {
                    "performed": False,
                    "action": args.action,
                    "error": str(exc),
                }
            state = fetch_state(client)
    except Exception as exc:
        logger.exception("Erreur pipeline")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # JSON complet
    (args.out_dir / "report.json").write_text(
        json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.monotonic() - started, 2),
            "action_result": action_result,
            "me": state.get("me"),
            "collections": state.get("collections"),
            "models": state.get("models"),
            "errors": state.get("errors"),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # HTML browsable
    (args.out_dir / "report.html").write_text(render_html(state, action_result), encoding="utf-8")

    # Markdown pour Job Summary (GitHub Actions)
    md = render_markdown(state, action_result)
    (args.out_dir / "report.md").write_text(md, encoding="utf-8")
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_env:
        try:
            Path(summary_env).write_text(md, encoding="utf-8")
            logger.info("Job Summary écrit dans %s", summary_env)
        except OSError as exc:
            logger.warning("Impossible d'ecrire Job Summary : %s", exc)

    # Log console
    print("---")
    print(f"Action : {action_result.get('action')} ({'OK' if action_result.get('performed') else 'skip/refresh'})")
    print(f"Collections : {len(state.get('collections') or [])}")
    print(f"Modeles : {len(state.get('models') or [])}")
    if state.get("errors"):
        print(f"Erreurs : {state['errors']}")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

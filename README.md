# Atelier RAG Albert (GitHub)

Découpe des documents Markdown en segments et les envoie dans une collection
[Albert (Etalab)](https://albert.api.etalab.gouv.fr) pour un pipeline RAG.

Version GitHub avec :

- **Workflow** dans `.github/workflows/segment-and-upload.yml` : validate →
  prepare (clone) → segment (Python + langchain) → upload (Python + Albert API)
- **UI web** (`docs/`) déployée en GitHub Pages : gère les collections Albert
  (list, create, delete, empty) et déclenche les workflows.
- **Zéro serveur maison** : tout tourne sur GitHub Actions et dans le navigateur.

Équivalent GitLab (miroir) : `forge.apps.education.fr/laurentabbal/chat-doc-atelier-rag-albert`.

## Structure

```
Atelier-RAG-Albert/
├── .github/workflows/
│   ├── ci.yml                       Tests pytest sur push/PR
│   └── segment-and-upload.yml       Workflow principal
├── app/
│   ├── cli.py                       Segmente un dépôt Markdown
│   ├── upload.py                    Uploade segments.jsonl vers Albert
│   ├── albert_client.py             Client HTTP Albert
│   ├── preprocessing.py             Front matter, 11ty, mojibake, containers
│   ├── segmenting.py                langchain markdown-aware
│   └── jsonl.py                     Streaming JSONL
├── docs/                            UI web (déployée en GitHub Pages)
│   ├── index.html
│   └── assets/{app.css,app.js}
├── tests/                           pytest
└── requirements.txt                 langchain-text-splitters, pyyaml, httpx
```

## Configuration côté GitHub

### 1. Secret ALBERT_API_KEY

Settings > Secrets and variables > Actions > **New repository secret** :

- Name : `ALBERT_API_KEY`
- Value : `sk-eyJhbGci...`

Utilisé par le job `upload` du workflow. Masqué automatiquement des logs.

### 2. Personal Access Token (optionnel)

Uniquement si tu veux déclencher les workflows depuis la UI web :

Genère un [fine-grained PAT](https://github.com/settings/tokens?type=beta) :
- Repository access : sélectionner uniquement ce repo
- Permissions : `Actions` = Read and write, `Metadata` = Read-only
- Le token commence par `github_pat_...`

### 3. GitHub Pages

Settings > Pages :
- Source : Deploy from a branch
- Branch : `main`
- Folder : `/docs`

URL finale : `https://chat-doc.github.io/Atelier-RAG-Albert/`

## Utilisation

### A. Depuis l'UI GitHub Actions (sans token)

1. Actions > Segment and upload to Albert > **Run workflow**
2. Remplir les inputs (target_repo_url, target_ref, content_dir, site_base_url,
   albert_collection_id, dry_run, empty_collection_first)
3. Run

### B. Depuis la UI web (avec PAT GitHub)

1. Ouvrir `https://chat-doc.github.io/Atelier-RAG-Albert/`
2. Coller la clé Albert dans le header
3. (Optionnel) Coller le PAT GitHub dans le header
4. Sélectionner ou créer une collection
5. Cliquer "Lancer un pipeline RAG"
6. Remplir les paramètres, valider

### C. En local

```bash
pip install -r requirements.txt

# Segmenter
python -m app.cli \
  --source-dir /path/to/repo \
  --content-dir content \
  --site-base-url https://... \
  --output segments.jsonl \
  --report report.json

# Simuler l'upload (dry-run)
python -m app.upload \
  --segments segments.jsonl \
  --report upload_report.json \
  --collection-id 123 \
  --api-key sk-... \
  --dry-run

# Vraiment uploader
python -m app.upload \
  --segments segments.jsonl \
  --report upload_report.json \
  --collection-id 123 \
  --api-key sk-...
```

## Sécurité

- **Clé Albert** : jamais dans le repo. En secret GitHub (pour le workflow)
  ou en localStorage du navigateur (pour la UI web).
- **PAT GitHub** : fine-grained, scope minimal (Actions + Metadata sur un
  seul repo).
- **UI web** : Content Security Policy strict, seuls `albert.api.etalab.gouv.fr`
  et `api.github.com` sont autorisés dans `connect-src`.

## Ce qu'il faut adapter si tu forkes

- `.github/workflows/segment-and-upload.yml` : les defaults dans la section
  `inputs:` (repo cible, collection ID par défaut, etc.)
- `docs/assets/app.js` : constantes `REPO_OWNER`, `REPO_NAME`, `WORKFLOW_FILE`
  en tête de fichier.

## Licence

MIT. Voir [LICENSE](LICENSE).

/**
 * Atelier RAG Albert (GitHub) - JS minimal de la Pages.
 *
 * Design "form only" :
 *   - Aucune connexion Albert cote client (evite CORS)
 *   - Le formulaire declenche le workflow Actions, la CI fait tout
 *   - Deux modes :
 *       (a) sans PAT   : ouvre github.com/actions/workflows/... (user confirme)
 *       (b) avec PAT   : POST /dispatches, direct
 *   - PAT en sessionStorage : supprime a la fermeture de l'onglet
 *
 * Constantes a adapter en cas de fork :
 *   - REPO_OWNER, REPO_NAME, WORKFLOW_FILE
 */
'use strict';

const REPO_OWNER = 'chat-doc';
const REPO_NAME = 'Atelier-RAG-Albert';
const WORKFLOW_FILE = 'albert-action.yml';
const GITHUB_BASE = 'https://api.github.com';
const STORAGE_TOKEN = 'atelier.github_token';

const state = { githubToken: null };

/* ============ STORAGE (sessionStorage : ephemere) ============ */

function loadToken() {
    state.githubToken = sessionStorage.getItem(STORAGE_TOKEN) || null;
}
function saveToken(v) {
    if (v) sessionStorage.setItem(STORAGE_TOKEN, v);
    else sessionStorage.removeItem(STORAGE_TOKEN);
    state.githubToken = v || null;
}
function clearToken() {
    sessionStorage.removeItem(STORAGE_TOKEN);
    state.githubToken = null;
}

/* ============ VISIBILITE ============ */

function refreshUI() {
    const hasToken = !!state.githubToken;
    document.querySelectorAll('[data-show-when]').forEach(el => {
        const cond = el.getAttribute('data-show-when');
        let show = false;
        if (cond === 'has-token') show = hasToken;
        else if (cond === 'no-token') show = !hasToken;
        el.classList.toggle('hidden', !show);
    });
    const badge = document.getElementById('token-badge');
    if (badge) {
        if (hasToken) {
            badge.className = 'badge ready';
            badge.textContent = 'PAT actif (session)';
        } else {
            badge.className = 'badge';
            badge.textContent = 'Token GitHub (optionnel)';
        }
    }
    // Lien onglet Actions
    const link = document.getElementById('link-actions');
    if (link) link.href = `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}`;
    // Champs contextuels
    updateActionFields();
}

function updateActionFields() {
    const form = document.getElementById('pipeline-form');
    const actionValue = form.querySelector('[name="action"]').value;
    document.querySelectorAll('[data-show-for]').forEach(el => {
        const forActions = el.getAttribute('data-show-for').split(/\s+/);
        el.classList.toggle('hidden', !forActions.includes(actionValue));
    });
}

/* ============ MESSAGES ============ */

function showMessage(type, title, text) {
    const block = document.getElementById('message-block');
    block.classList.remove('warning-block', 'success-block');
    block.classList.add(type === 'error' ? 'warning-block' : 'success-block');
    block.classList.remove('hidden');
    document.querySelector('[data-bind="message-title"]').textContent = title;
    document.querySelector('[data-bind="message-text"]').textContent = text;
    if (type === 'error') console.error(`[${title}]`, text);
    else setTimeout(() => block.classList.add('hidden'), 8000);
}

/* ============ HELPERS ============ */

function collectInputs(form) {
    const data = new FormData(form);
    return {
        action: data.get('action'),
        collection_id: (data.get('collection_id') || '').trim(),
        name: (data.get('name') || '').trim(),
        description: (data.get('description') || '').trim(),
        visibility: data.get('visibility') || 'private',
    };
}

function validateInputs(inputs) {
    const { action } = inputs;
    if (action === 'create' && !inputs.name) {
        return 'Le nom est obligatoire pour create.';
    }
    if (['rename', 'empty', 'delete'].includes(action) && !inputs.collection_id) {
        return "L'ID de collection est obligatoire pour rename/empty/delete.";
    }
    return null;
}

/* ============ MODE A : ouvrir Actions UI ============ */

function openActionsUI() {
    const url = `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}`;
    window.open(url, '_blank', 'noreferrer');
    showMessage('success', 'Redirection', "GitHub Actions ouvert dans un nouvel onglet. Clique 'Run workflow' et remplis les inputs.");
}

/* ============ MODE B : trigger via API ============ */

async function triggerViaAPI(inputs) {
    const url = `${GITHUB_BASE}/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
    const res = await fetch(url, {
        method: 'POST',
        headers: {
            'Accept': 'application/vnd.github+json',
            'Authorization': `Bearer ${state.githubToken}`,
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main', inputs }),
    });
    if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`GitHub HTTP ${res.status} : ${body.substring(0, 300)}`);
    }
}

/* ============ WIRE ============ */

document.addEventListener('DOMContentLoaded', () => {
    loadToken();
    refreshUI();
});

document.addEventListener('change', (event) => {
    if (event.target.name === 'action') updateActionFields();
});

document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    if (action === 'save-token') {
        const v = document.getElementById('github-token-input').value.trim();
        if (v) saveToken(v);
        document.getElementById('github-token-input').value = '';
        document.getElementById('token-details').open = false;
        refreshUI();
        if (v) showMessage('success', 'OK', 'PAT stocké en sessionStorage.');
    } else if (action === 'clear-storage') {
        clearToken();
        refreshUI();
        showMessage('success', 'OK', 'PAT effacé.');
    }
});

document.getElementById('pipeline-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const inputs = collectInputs(form);
    const err = validateInputs(inputs);
    if (err) {
        showMessage('error', 'Validation', err);
        return;
    }
    if (!state.githubToken) {
        openActionsUI();
        return;
    }
    try {
        await triggerViaAPI(inputs);
        showMessage('success', 'OK',
            `Workflow "${inputs.action}" déclenché. Ouvre l'onglet Actions pour suivre.`);
        window.open(`https://github.com/${REPO_OWNER}/${REPO_NAME}/actions`, '_blank', 'noreferrer');
    } catch (e) {
        showMessage('error', 'Erreur', e.message);
    }
});

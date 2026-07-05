/**
 * Atelier RAG Albert - JS de la GitHub Pages.
 *
 * Stateless côté serveur : toutes les clés sont dans localStorage,
 * jamais transmises à autre chose que albert.api.etalab.gouv.fr et
 * api.github.com (voir CSP dans index.html).
 *
 * Deux tokens :
 *   - albert_key      : indispensable pour lister/gérer les collections
 *   - github_token    : optionnel, pour déclencher les workflows depuis
 *                       cette page au lieu d'aller sur github.com
 *
 * Configuration du repo :
 *   - Modifier les constantes REPO_OWNER, REPO_NAME, WORKFLOW_FILE
 *     ci-dessous si tu forkes ce projet.
 */
'use strict';

const REPO_OWNER = 'chat-doc';
const REPO_NAME = 'Atelier-RAG-Albert';
const WORKFLOW_FILE = 'segment-and-upload.yml';
const ALBERT_BASE = 'https://albert.api.etalab.gouv.fr';
const GITHUB_BASE = 'https://api.github.com';
const STORAGE_ALBERT = 'atelier.albert_key';
const STORAGE_GITHUB = 'atelier.github_token';
const STORAGE_SELECTED = 'atelier.selected_collection';
const QUOTA_BYTES = 10 * 1000 * 1000;

/* ============ ETAT ============ */

const state = {
    albertKey: null,
    githubToken: null,
    meInfo: null,
    collections: [],
    models: [],
    selectedId: null,
    selectedDetails: null,
    myEmail: null,
};

/* ============ STORAGE ============ */

function loadTokens() {
    state.albertKey = localStorage.getItem(STORAGE_ALBERT) || null;
    state.githubToken = localStorage.getItem(STORAGE_GITHUB) || null;
    state.selectedId = localStorage.getItem(STORAGE_SELECTED) || null;
}
function saveAlbertKey(v) {
    if (v) localStorage.setItem(STORAGE_ALBERT, v);
    else localStorage.removeItem(STORAGE_ALBERT);
    state.albertKey = v || null;
}
function saveGithubToken(v) {
    if (v) localStorage.setItem(STORAGE_GITHUB, v);
    else localStorage.removeItem(STORAGE_GITHUB);
    state.githubToken = v || null;
}
function saveSelected(id) {
    if (id) localStorage.setItem(STORAGE_SELECTED, id);
    else localStorage.removeItem(STORAGE_SELECTED);
    state.selectedId = id || null;
}
function clearStorage() {
    localStorage.removeItem(STORAGE_ALBERT);
    localStorage.removeItem(STORAGE_GITHUB);
    localStorage.removeItem(STORAGE_SELECTED);
    state.albertKey = null;
    state.githubToken = null;
    state.selectedId = null;
    state.meInfo = null;
    state.collections = [];
    state.selectedDetails = null;
    state.myEmail = null;
}

/* ============ FETCH HELPERS ============ */

async function albertFetch(path, options = {}) {
    if (!state.albertKey) throw new Error('Clé Albert manquante.');
    const url = ALBERT_BASE + path;
    const headers = Object.assign({
        'Authorization': `Bearer ${state.albertKey}`,
        'Accept': 'application/json',
    }, options.headers || {});
    if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Albert HTTP ${res.status} : ${body.substring(0, 400)}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

async function githubFetch(path, options = {}) {
    if (!state.githubToken) throw new Error('Token GitHub manquant.');
    const url = GITHUB_BASE + path;
    const headers = Object.assign({
        'Accept': 'application/vnd.github+json',
        'Authorization': `Bearer ${state.githubToken}`,
        'X-GitHub-Api-Version': '2022-11-28',
    }, options.headers || {});
    if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
    }
    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`GitHub HTTP ${res.status} : ${body.substring(0, 400)}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

/* ============ FORMAT ============ */

function humanBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ['KB', 'MB', 'GB'];
    let v = bytes / 1024, u = 'KB';
    for (const unit of units) {
        u = unit;
        if (v < 1024) break;
        v /= 1024;
    }
    return `${v.toFixed(v < 10 ? 2 : 1)} ${u}`;
}
function fmtTimestamp(v) {
    if (v === null || v === undefined || v === '') return '-';
    if (typeof v === 'number' || /^\d+$/.test(v)) {
        return new Date(Number(v) * 1000).toISOString().slice(0, 16).replace('T', ' ');
    }
    return String(v);
}
function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ============ MESSAGES ============ */

function showMessage(type, title, text) {
    const block = document.getElementById('message-block');
    block.classList.remove('warning-block', 'success-block');
    block.classList.add(type === 'error' ? 'warning-block' : 'success-block');
    block.classList.remove('hidden');
    document.querySelector('[data-bind="message-title"]').textContent = title;
    document.querySelector('[data-bind="message-text"]').textContent = text;
    setTimeout(() => block.classList.add('hidden'), 6000);
}

/* ============ VISIBILITE (data-show-when / data-hidden-when) ============ */

function refreshVisibility() {
    const connected = !!state.albertKey && !!state.meInfo;
    const collectionSelected = connected && !!state.selectedDetails;
    const quotaFull = collectionSelected && (state.selectedDetails.size || 0) >= 0.95 * QUOTA_BYTES;

    document.querySelectorAll('[data-show-when]').forEach(el => {
        const cond = el.getAttribute('data-show-when');
        let show = false;
        if (cond === 'connected') show = connected;
        else if (cond === 'disconnected') show = !connected;
        else if (cond === 'collection-selected') show = collectionSelected;
        else if (cond === 'quota-full') show = quotaFull;
        el.classList.toggle('hidden', !show);
    });
    document.querySelectorAll('[data-hidden-when]').forEach(el => {
        const cond = el.getAttribute('data-hidden-when');
        let hide = false;
        if (cond === 'connected') hide = connected;
        else if (cond === 'disconnected') hide = !connected;
        el.classList.toggle('hidden', hide);
    });

    // Token badge dans le header
    const badge = document.getElementById('token-badge');
    if (badge) {
        if (connected) {
            badge.className = 'badge ready';
            badge.textContent = 'Connecté';
        } else if (state.albertKey) {
            badge.className = 'badge error';
            badge.textContent = 'Clé Albert invalide';
        } else {
            badge.className = 'badge';
            badge.textContent = 'Clé Albert requise';
        }
    }
    document.querySelector('[data-bind="user-email"]').textContent =
        state.myEmail || '';
}

/* ============ RENDU CONTENU ============ */

function renderSelectedBanner() {
    const c = state.selectedDetails;
    if (!c) return;
    document.querySelector('[data-bind="selected-name"]').textContent = c.name || 'Sans nom';
    document.querySelector('[data-bind="selected-id"]').textContent = c.id;
    document.querySelector('[data-bind="selected-owner"]').textContent = c.owner || '-';
    document.querySelector('[data-bind="selected-description"]').textContent = c.description || '(aucune)';
    document.querySelector('[data-bind="selected-created"]').textContent = fmtTimestamp(c.created);
    document.querySelector('[data-bind="selected-updated"]').textContent = fmtTimestamp(c.updated);
    document.querySelector('[data-bind="selected-documents"]').textContent = c.documents || 0;
    document.querySelector('[data-bind="selected-visibility"]').textContent = c.visibility || '-';

    const sizeBytes = c.size || 0;
    const sizePct = Math.min(100, Math.round(sizeBytes / QUOTA_BYTES * 100));
    const sizeEl = document.querySelector('[data-bind="selected-size"]');
    sizeEl.textContent = `${humanBytes(sizeBytes)} (${sizePct}%)`;
    sizeEl.className = sizePct >= 95 ? 'quota-full' : sizePct >= 80 ? 'quota-warn' : '';
}

function renderCollectionsList() {
    const my = [], other = [];
    for (const col of state.collections) {
        const owner = (col.owner || '').toLowerCase();
        const isMine = state.myEmail && owner === state.myEmail.toLowerCase();
        (isMine ? my : other).push(col);
    }
    document.querySelector('[data-bind="my-count"]').textContent = my.length;
    document.querySelector('[data-bind="other-count"]').textContent = other.length;

    const myBody = document.querySelector('[data-bind="my-collections-body"]');
    const otherBody = document.querySelector('[data-bind="other-collections-body"]');

    const renderRow = (col, includeOwner) => {
        const isSel = String(col.id) === String(state.selectedId);
        const sel = isSel ? '<span class="badge ready">sélectionnée</span>' :
            `<button type="button" data-action="select-collection" data-id="${escapeHtml(col.id)}">Sélectionner</button>`;
        return `
            <tr class="${isSel ? 'row-selected' : ''}">
                <td class="mono">${escapeHtml(col.id)}</td>
                <td>${escapeHtml(col.name || 'sans nom')}</td>
                <td class="right">${col.documents || 0}</td>
                <td class="right">${humanBytes(col.size || 0)}</td>
                ${includeOwner ? `<td class="mono small">${escapeHtml(col.owner || '-')}</td>` : `<td>${escapeHtml(col.visibility || '-')}</td>`}
                <td>${fmtTimestamp(col.created)}</td>
                <td class="right row-actions">${sel}</td>
            </tr>`;
    };

    myBody.innerHTML = my.length
        ? my.map(c => renderRow(c, false)).join('')
        : '<tr><td colspan="7" class="muted small">Aucune collection.</td></tr>';
    otherBody.innerHTML = other.length
        ? other.map(c => renderRow(c, true)).join('')
        : '<tr><td colspan="7" class="muted small">-</td></tr>';
}

function renderMeInfo() {
    const me = state.meInfo;
    if (!me) return;
    document.querySelector('[data-bind="me-email"]').textContent = me.email || '-';
    document.querySelector('[data-bind="me-name"]').textContent = me.name || '-';
    document.querySelector('[data-bind="me-id"]').textContent = me.id || '-';
    document.querySelector('[data-bind="me-org"]').textContent = me.organization || '-';
    document.querySelector('[data-bind="me-priority"]').textContent = me.priority || '-';
    document.querySelector('[data-bind="me-expires"]').textContent = fmtTimestamp(me.expires);
    const perms = Array.isArray(me.permissions) && me.permissions.length ? me.permissions.join(', ') : '(aucune)';
    document.querySelector('[data-bind="me-permissions"]').textContent = perms;
}

function renderModels() {
    document.querySelector('[data-bind="models-count"]').textContent = state.models.length;
    const body = document.querySelector('[data-bind="models-body"]');
    body.innerHTML = state.models.length
        ? state.models.map(m => `
            <tr>
                <td class="mono small">${escapeHtml(m.id || '-')}</td>
                <td class="small">${escapeHtml(m.type || '-')}</td>
                <td class="small">${escapeHtml((m.aliases || []).join(', '))}</td>
                <td class="right mono small">${escapeHtml(m.max_context_length || '-')}</td>
                <td class="right mono small">${escapeHtml(m.costs && m.costs.prompt_tokens != null ? m.costs.prompt_tokens : '-')}</td>
                <td class="right mono small">${escapeHtml(m.costs && m.costs.completion_tokens != null ? m.costs.completion_tokens : '-')}</td>
            </tr>`).join('')
        : '<tr><td colspan="6" class="muted small">-</td></tr>';
}

function fillModalForRename() {
    const c = state.selectedDetails;
    if (!c) return;
    const modal = document.getElementById('modal-rename');
    modal.querySelector('[name="name"]').value = c.name || '';
    modal.querySelector('[name="description"]').value = c.description || '';
    modal.querySelector('[name="visibility"]').value = c.visibility || 'private';
}

/* ============ ACTIONS ALBERT ============ */

async function loadAlbertData() {
    if (!state.albertKey) return;
    try {
        showOverlay('Connexion à Albert...');
        state.meInfo = await albertFetch('/v1/me/info');
        state.myEmail = state.meInfo.email;
        const collectionsResp = await albertFetch('/v1/collections?limit=100&order_by=id&order_direction=asc');
        state.collections = collectionsResp.data || [];
        const modelsResp = await albertFetch('/v1/models');
        state.models = modelsResp.data || [];
        if (state.selectedId) {
            await loadSelectedCollection();
        }
        renderAll();
    } catch (err) {
        showMessage('error', 'Erreur Albert', err.message);
        state.meInfo = null;
        state.myEmail = null;
    } finally {
        hideOverlay();
    }
    refreshVisibility();
}

async function loadSelectedCollection() {
    if (!state.selectedId) {
        state.selectedDetails = null;
        return;
    }
    try {
        state.selectedDetails = await albertFetch(`/v1/collections/${state.selectedId}`);
    } catch (err) {
        state.selectedDetails = null;
        showMessage('error', 'Collection inaccessible', err.message);
    }
}

async function refreshCollections() {
    try {
        showOverlay('Rafraîchissement...');
        const collectionsResp = await albertFetch('/v1/collections?limit=100&order_by=id&order_direction=asc');
        state.collections = collectionsResp.data || [];
        if (state.selectedId) await loadSelectedCollection();
        renderAll();
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

async function actionSelectCollection(id) {
    saveSelected(id);
    await loadSelectedCollection();
    renderAll();
    refreshVisibility();
}

async function actionCreateCollection(form) {
    const name = form.name.value.trim();
    if (!name) return;
    try {
        showOverlay('Création...');
        const body = {
            name,
            description: form.description.value.trim() || undefined,
            visibility: form.visibility.value,
        };
        const created = await albertFetch('/v1/collections', {
            method: 'POST',
            body: JSON.stringify(body),
        });
        showMessage('success', 'OK', `Collection créée (id ${created.id}).`);
        if (form.auto_select.checked && created.id) {
            saveSelected(String(created.id));
        }
        await refreshCollections();
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

async function actionRenameCollection(form) {
    if (!state.selectedDetails) return;
    try {
        showOverlay('Enregistrement...');
        const body = {
            name: form.name.value.trim() || undefined,
            description: form.description.value.trim() || undefined,
            visibility: form.visibility.value,
        };
        await albertFetch(`/v1/collections/${state.selectedDetails.id}`, {
            method: 'PATCH',
            body: JSON.stringify(body),
        });
        showMessage('success', 'OK', 'Collection mise à jour.');
        await refreshCollections();
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

async function actionEmptyCollection(form) {
    if (form.confirm.value.trim() !== 'VIDER') {
        showMessage('error', 'Confirmation invalide', 'Taper VIDER pour confirmer.');
        return;
    }
    if (!state.selectedDetails) return;
    try {
        showOverlay('Suppression des documents...');
        const docs = await albertFetch(`/v1/collections/${state.selectedDetails.id}/documents?limit=100`);
        const list = docs.data || docs.documents || [];
        let deleted = 0, errors = 0;
        for (const doc of list) {
            try {
                await albertFetch(`/v1/documents/${doc.id}`, { method: 'DELETE' });
                deleted++;
            } catch (e) { errors++; }
        }
        showMessage(errors === 0 ? 'success' : 'error', 'Vidée',
            `${deleted} documents supprimés, ${errors} erreurs.`);
        await refreshCollections();
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

async function actionDeleteCollection(form) {
    if (form.confirm.value.trim() !== 'SUPPRIMER') {
        showMessage('error', 'Confirmation invalide', 'Taper SUPPRIMER pour confirmer.');
        return;
    }
    if (!state.selectedDetails) return;
    const id = state.selectedDetails.id;
    try {
        showOverlay('Destruction...');
        await albertFetch(`/v1/collections/${id}`, { method: 'DELETE' });
        showMessage('success', 'OK', `Collection ${id} supprimée.`);
        saveSelected(null);
        state.selectedDetails = null;
        await refreshCollections();
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

/* ============ TRIGGER WORKFLOW ============ */

async function actionTriggerPipeline(form) {
    if (!state.selectedId) {
        showMessage('error', 'Aucune collection', 'Sélectionne une collection cible.');
        return;
    }
    const inputs = {
        target_repo_url: form.target_repo_url.value.trim(),
        target_ref: form.target_ref.value.trim() || 'main',
        content_dir: form.content_dir.value.trim(),
        site_base_url: form.site_base_url.value.trim(),
        albert_collection_id: String(state.selectedId),
        empty_collection_first: form.empty_collection_first.checked ? 'true' : 'false',
        dry_run: form.dry_run.checked ? 'true' : 'false',
    };

    // Si pas de token GitHub, ouvrir l'UI GitHub Actions
    if (!state.githubToken) {
        const params = new URLSearchParams();
        Object.entries(inputs).forEach(([k, v]) => params.set(k, v));
        const url = `https://github.com/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}?${params.toString()}`;
        showMessage('success', 'Redirection', `Configure un token GitHub dans le header pour déclencher directement d'ici. Ouverture de github.com...`);
        window.open(url, '_blank', 'noreferrer');
        return;
    }

    // Sinon, trigger via API
    try {
        showOverlay('Déclenchement du workflow...');
        await githubFetch(`/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`, {
            method: 'POST',
            body: JSON.stringify({ ref: 'main', inputs }),
        });
        showMessage('success', 'OK', 'Workflow déclenché. Regarde github.com/actions pour suivre.');
        window.open(`https://github.com/${REPO_OWNER}/${REPO_NAME}/actions`, '_blank', 'noreferrer');
    } catch (err) {
        showMessage('error', 'Erreur', err.message);
    } finally {
        hideOverlay();
    }
}

/* ============ OVERLAY ============ */

let overlayEl = null;
function showOverlay(message) {
    if (overlayEl) overlayEl.remove();
    overlayEl = document.createElement('div');
    overlayEl.className = 'app-loading-overlay';
    overlayEl.innerHTML = `
        <div class="loading-card">
            <div class="spinner"></div>
            <div><strong>${escapeHtml(message)}</strong></div>
        </div>`;
    document.body.appendChild(overlayEl);
}
function hideOverlay() {
    if (overlayEl) { overlayEl.remove(); overlayEl = null; }
}

/* ============ MODALS ============ */

document.addEventListener('click', function (event) {
    const openBtn = event.target.closest('[data-action="open-modal"]');
    if (openBtn) {
        event.preventDefault();
        const id = openBtn.getAttribute('data-modal');
        const dialog = document.getElementById(id);
        if (!dialog) return;
        if (id === 'modal-rename') fillModalForRename();
        dialog.showModal();
        return;
    }
    const closeBtn = event.target.closest('[data-close-modal]');
    if (closeBtn) {
        event.preventDefault();
        const dialog = closeBtn.closest('dialog');
        if (dialog) dialog.close();
    }
});

/* ============ WIRE ACTIONS ============ */

document.addEventListener('click', async function (event) {
    const btn = event.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    if (action === 'save-tokens') {
        const a = document.getElementById('albert-token-input').value.trim();
        const g = document.getElementById('github-token-input').value.trim();
        if (a) saveAlbertKey(a);
        if (g) saveGithubToken(g);
        document.getElementById('albert-token-input').value = '';
        document.getElementById('github-token-input').value = '';
        document.getElementById('token-details').open = false;
        await loadAlbertData();
    } else if (action === 'clear-storage') {
        if (!confirm('Vider les tokens et sélection stockés en local ?')) return;
        clearStorage();
        renderAll();
        refreshVisibility();
        showMessage('success', 'OK', 'Stockage local vidé.');
    } else if (action === 'refresh-collections') {
        await refreshCollections();
    } else if (action === 'select-collection') {
        const id = btn.getAttribute('data-id');
        await actionSelectCollection(id);
    }
});

document.addEventListener('submit', async function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const key = form.getAttribute('data-form');
    if (!key) return;
    event.preventDefault();
    const dialog = form.closest('dialog');
    if (dialog) dialog.close();
    if (key === 'create-collection') await actionCreateCollection(form);
    else if (key === 'rename-collection') await actionRenameCollection(form);
    else if (key === 'empty-collection') await actionEmptyCollection(form);
    else if (key === 'delete-collection') await actionDeleteCollection(form);
    else if (key === 'trigger-pipeline') await actionTriggerPipeline(form);
});

/* ============ RENDU GLOBAL ============ */

function renderAll() {
    renderSelectedBanner();
    renderCollectionsList();
    renderMeInfo();
    renderModels();
    refreshVisibility();
}

/* ============ BOOT ============ */

loadTokens();
refreshVisibility();
if (state.albertKey) {
    loadAlbertData();
} else {
    // Ouvre le panneau de saisie du token si aucun n'est stocké
    document.getElementById('token-details').open = true;
}

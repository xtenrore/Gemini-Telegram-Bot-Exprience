const API = '/admin/api';
const REFRESH_SECONDS = 30;
let masterPassword = sessionStorage.getItem('planeAdminPassword') || '';
let delegatedToken = sessionStorage.getItem('planeAdminToken') || '';
let sessionInfo = null;
let userOffset = 0;
let userTotal = 0;
let countdown = REFRESH_SECONDS;
let refreshHandle = null;
let searchHandle = null;

function readInviteToken() {
    if (!location.hash.startsWith('#token=')) return;
    const token = decodeURIComponent(location.hash.slice(7));
    if (token) {
        delegatedToken = token;
        masterPassword = '';
        sessionStorage.setItem('planeAdminToken', token);
        sessionStorage.removeItem('planeAdminPassword');
    }
    history.replaceState(null, '', location.pathname + location.search);
}

function authHeaders() {
    if (delegatedToken) return { Authorization: `Bearer ${delegatedToken}` };
    if (masterPassword) return { Authorization: `Basic ${btoa(`admin:${masterPassword}`)}` };
    return {};
}

async function apiFetch(path, options = {}) {
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    const response = await fetch(`${API}${path}`, { ...options, headers });
    if (response.status === 401) {
        lockDashboard('Authentication failed. Enter the production ADMIN_PASSWORD or use a valid delegated admin link.');
        throw new Error('Unauthorized');
    }
    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
}

function lockDashboard(message = '') {
    sessionInfo = null;
    delegatedToken = '';
    masterPassword = '';
    sessionStorage.removeItem('planeAdminToken');
    sessionStorage.removeItem('planeAdminPassword');
    document.getElementById('loginGate').classList.remove('hidden');
    document.getElementById('loginError').textContent = message;
    setStatus('error', 'Locked');
}

async function authenticate() {
    try {
        sessionInfo = await apiFetch('/v36/session');
        document.getElementById('loginGate').classList.add('hidden');
        renderSession();
        setStatus('online', 'Connected');
        await fetchAll();
        startRefreshTimer();
    } catch (error) {
        if (!String(error.message).includes('Unauthorized')) showToast(error.message, true);
    }
}

function renderSession() {
    const pill = document.getElementById('sessionPill');
    if (!sessionInfo) return;
    pill.className = `session-pill ${sessionInfo.root ? 'root' : 'delegated'}`;
    pill.textContent = sessionInfo.root ? 'Root admin' : `Delegated admin #${sessionInfo.user_id}`;
}

function startRefreshTimer() {
    countdown = REFRESH_SECONDS;
    clearInterval(refreshHandle);
    refreshHandle = setInterval(async () => {
        countdown -= 1;
        document.getElementById('refreshTimer').textContent = `${countdown}s`;
        if (countdown <= 0) {
            countdown = REFRESH_SECONDS;
            await fetchOperational();
        }
    }, 1000);
}

async function fetchAll() {
    await Promise.allSettled([fetchStats(), fetchOperational(), fetchUsers(), fetchAudit()]);
}

async function fetchOperational() {
    await Promise.allSettled([fetchOverview(), fetchSystem(), fetchProviders(), fetchNotifications(), fetchAudit()]);
}

async function fetchStats() {
    try {
        const data = await apiFetch('/v36/admin-stats');
        setText('totalUsers', data.users);
        setText('priorityUsers', data.priority_users);
        setText('delegatedAdmins', data.delegated_admins);
        setText('pausedUsers', data.notifications_paused);
        setText('customDelayUsers', data.custom_delay_users);
    } catch (error) { showToast(`Stats: ${error.message}`, true); }
}

async function fetchOverview() {
    try {
        const data = await apiFetch('/overview');
        const cycle = data.cycle_stats || {};
        if (cycle.last_cycle_time) {
            const ago = Math.max(0, Math.round(Date.now() / 1000 - cycle.last_cycle_time));
            setText('lastCycle', `${ago}s ago`);
        } else setText('lastCycle', '-');
    } catch (error) { showToast(`Overview: ${error.message}`, true); }
}

async function fetchSystem() {
    try {
        const data = await apiFetch('/system');
        const worker = data.worker || {};
        setDetailRows('systemWorker', {
            'Cycles': worker.total_cycles ?? '-',
            'Last cycle': worker.last_cycle_duration_ms ? `${worker.last_cycle_duration_ms} ms` : '-',
            'Status': worker.worker_status || 'running',
        });
        const cfg = data.config || {};
        setDetailRows('systemConfig', {
            'Base tick': `${cfg.poll_interval_seconds ?? 5}s`,
            'Default radius': `${cfg.default_radius_km ?? '-'} km`,
            'Cooldown': `${cfg.cooldown_minutes ?? '-'} min`,
            'Priority cadence': '5s hot region',
        });
        const db = data.database || {};
        setDetailRows('systemDatabase', db.error ? { Error: db.error } : {
            'Users': db.users ?? '-', 'Locations': db.locations ?? '-', 'Preferences': db.preferences ?? '-', 'Notifications': db.notifications ?? '-'
        });
        setDetailRows('systemPlatform', { 'OS': data.platform || '-', 'Python': data.python_version || '-', 'Uptime': formatUptime(data.uptime_seconds) });
    } catch (error) { showToast(`System: ${error.message}`, true); }
}

async function fetchProviders() {
    try {
        const data = await apiFetch('/providers');
        const body = document.getElementById('providersBody');
        const providers = data.providers || [];
        body.innerHTML = providers.length ? providers.map(p => `<tr>
            <td><strong>${esc(p.name)}</strong></td>
            <td>${p.is_unlimited ? badge('Unlimited', 'success') : badge('Credit-limited', 'warning')}</td>
            <td>${fmt(p.request_count)}</td><td>${fmt(p.error_count)}</td>
            <td>${p.last_success_time ? new Date(p.last_success_time * 1000).toLocaleTimeString() : '-'}</td>
            <td>${p.can_request_now ? badge('Ready', 'success') : badge('Rate limited', 'muted')}</td>
        </tr>`).join('') : '<tr><td colspan="6" class="loading">No provider data</td></tr>';
    } catch (error) { showToast(`Providers: ${error.message}`, true); }
}

async function fetchNotifications() {
    try {
        const rows = await apiFetch('/notifications?limit=30');
        const body = document.getElementById('notificationsBody');
        body.innerHTML = rows.length ? rows.map(n => `<tr>
            <td>${esc(n.user_id)}</td><td><code>${esc(n.aircraft_icao24)}</code></td><td>${esc(n.aircraft_type || '-')}</td>
            <td>${n.distance_km == null ? '-' : Number(n.distance_km).toFixed(1) + ' km'}</td><td>${formatTime(n.notified_at)}</td>
        </tr>`).join('') : '<tr><td colspan="5" class="loading">No notifications yet</td></tr>';
    } catch (error) { showToast(`Notifications: ${error.message}`, true); }
}

async function fetchUsers() {
    const search = document.getElementById('userSearch').value.trim();
    const limit = Number(document.getElementById('pageSize').value || 25);
    try {
        const data = await apiFetch(`/v36/users?search=${encodeURIComponent(search)}&limit=${limit}&offset=${userOffset}`);
        userTotal = data.total;
        renderUsers(data.items || []);
        const start = data.total ? data.offset + 1 : 0;
        const end = data.offset + (data.items || []).length;
        setText('usersPageInfo', `${start}-${end} of ${data.total}`);
        document.getElementById('prevUsers').disabled = data.offset <= 0;
        document.getElementById('nextUsers').disabled = !data.has_more;
    } catch (error) { showToast(`Users: ${error.message}`, true); }
}

function renderUsers(users) {
    const body = document.getElementById('usersBody');
    if (!users.length) {
        body.innerHTML = '<tr><td colspan="9" class="loading">No matching users</td></tr>';
        return;
    }
    body.innerHTML = users.map(u => {
        const c = u.controls || {};
        const username = u.username ? `@${u.username}` : 'no username';
        const initial = (u.first_name || u.username || '?').trim().slice(0, 1).toUpperCase();
        const adminActions = sessionInfo?.can_manage_admins
            ? (u.is_admin
                ? `<button class="btn btn-warning" onclick="copyAdminLink(${u.user_id})">Copy link</button><button class="btn btn-danger" onclick="setAdmin(${u.user_id}, false)">Revoke</button>`
                : `<button class="btn btn-success" onclick="setAdmin(${u.user_id}, true)">Grant admin</button>`)
            : (u.is_admin ? badge('Admin', 'warning') : '<span style="color:var(--text-muted)">—</span>');
        return `<tr data-user-id="${u.user_id}">
            <td><div class="user-cell"><span class="avatar">${esc(initial)}</span><span class="user-meta"><strong>${esc(u.first_name || username)}</strong><small>${esc(username)}</small></span></div></td>
            <td>${u.user_id}</td>
            <td><label class="switch"><input id="priority-${u.user_id}" type="checkbox" ${c.priority_enabled ? 'checked' : ''}><span></span></label></td>
            <td><input class="input compact" id="delay-${u.user_id}" type="number" min="5" max="120" step="1" value="${Number(c.delay_seconds || 5)}"> s</td>
            <td><input class="input compact" id="radius-${u.user_id}" type="number" min="1" max="250" step="1" value="${Number(u.location?.radius_km || 15)}"> km</td>
            <td><label class="switch"><input id="monitor-${u.user_id}" type="checkbox" ${u.setup_complete ? 'checked' : ''}><span></span></label></td>
            <td><label class="switch"><input id="notify-${u.user_id}" type="checkbox" ${c.notifications_enabled !== false ? 'checked' : ''}><span></span></label></td>
            <td><div class="action-stack">${adminActions}</div></td>
            <td><div class="action-stack"><button class="btn btn-primary" onclick="saveUser(${u.user_id})">Save</button><button class="btn" onclick="resetUser(${u.user_id})">Reset</button></div></td>
        </tr>`;
    }).join('');
}

async function saveUser(id) {
    const payload = {
        priority_enabled: document.getElementById(`priority-${id}`).checked,
        delay_seconds: Number(document.getElementById(`delay-${id}`).value),
        radius_km: Number(document.getElementById(`radius-${id}`).value),
        monitoring_enabled: document.getElementById(`monitor-${id}`).checked,
        notifications_enabled: document.getElementById(`notify-${id}`).checked,
    };
    try {
        await apiFetch(`/v36/user/${id}/controls`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        showToast(`Saved controls for user ${id}`);
        await Promise.all([fetchStats(), fetchUsers(), fetchAudit()]);
    } catch (error) { showToast(error.message, true); }
}

async function resetUser(id) {
    try {
        await apiFetch(`/v36/user/${id}/reset-controls`, { method: 'POST' });
        showToast(`Reset user ${id} to default admin controls`);
        await Promise.all([fetchStats(), fetchUsers(), fetchAudit()]);
    } catch (error) { showToast(error.message, true); }
}

async function setAdmin(id, enabled) {
    try {
        const result = await apiFetch(`/v36/user/${id}/admin-access`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) });
        if (enabled && result.token) await deliverAdminLink(id, result.token);
        else showToast(`Admin access revoked for user ${id}`);
        await Promise.all([fetchStats(), fetchUsers(), fetchAudit()]);
    } catch (error) { showToast(error.message, true); }
}

async function copyAdminLink(id) {
    try {
        const result = await apiFetch(`/v36/user/${id}/admin-access`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: true }) });
        await deliverAdminLink(id, result.token);
        await fetchAudit();
    } catch (error) { showToast(error.message, true); }
}

async function deliverAdminLink(id, token) {
    const link = `${location.origin}/admin#token=${encodeURIComponent(token)}`;
    try {
        await navigator.clipboard.writeText(link);
        showToast(`Admin link for user ${id} copied to clipboard`);
    } catch (_) {
        window.prompt('Copy this delegated admin link:', link);
    }
}

async function fetchAudit() {
    try {
        const rows = await apiFetch('/v36/audit?limit=25');
        const body = document.getElementById('auditBody');
        body.innerHTML = rows.length ? rows.map(r => `<tr>
            <td>${formatTime(r.created_at)}</td><td>${r.actor_kind === 'delegated' ? `User ${esc(r.actor_user_id)}` : esc(r.actor_kind)}</td>
            <td>${esc(r.action)}</td><td>${r.target_user_id == null ? '-' : esc(r.target_user_id)}</td><td class="audit-details">${esc(JSON.stringify(r.details || {}))}</td>
        </tr>`).join('') : '<tr><td colspan="5" class="loading">No admin actions yet</td></tr>';
    } catch (error) { showToast(`Audit: ${error.message}`, true); }
}

function setStatus(state, text) {
    document.getElementById('statusDot').className = `status-dot ${state}`;
    setText('statusText', text);
}
function setText(id, value) { const el = document.getElementById(id); if (el) el.textContent = String(value ?? '-'); }
function fmt(value) { return Number(value || 0).toLocaleString(); }
function formatTime(value) { if (!value || value === 'None') return '-'; const d = new Date(value); return Number.isNaN(d.getTime()) ? esc(value) : d.toLocaleString(); }
function formatUptime(seconds) { seconds = Number(seconds || 0); const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600), m = Math.floor((seconds % 3600) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`; }
function badge(text, type) { return `<span class="badge badge-${type}">${esc(text)}</span>`; }
function esc(value) { const div = document.createElement('div'); div.textContent = value == null ? '' : String(value); return div.innerHTML; }
function setDetailRows(id, values) { const el = document.getElementById(id); if (!el) return; el.innerHTML = Object.entries(values).map(([k,v]) => `<div class="detail-row"><span class="detail-label">${esc(k)}</span><span class="detail-value">${esc(v)}</span></div>`).join(''); }
let toastTimer = null;
function showToast(message, error = false) { const el = document.getElementById('toast'); el.textContent = message; el.className = `toast show${error ? ' error' : ''}`; clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.className = 'toast'; }, 3200); }

window.saveUser = saveUser;
window.resetUser = resetUser;
window.setAdmin = setAdmin;
window.copyAdminLink = copyAdminLink;

document.addEventListener('DOMContentLoaded', () => {
    readInviteToken();
    document.getElementById('loginButton').addEventListener('click', async () => {
        masterPassword = document.getElementById('adminPassword').value;
        delegatedToken = '';
        sessionStorage.setItem('planeAdminPassword', masterPassword);
        sessionStorage.removeItem('planeAdminToken');
        await authenticate();
    });
    document.getElementById('adminPassword').addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('loginButton').click(); });
    document.getElementById('logoutButton').addEventListener('click', () => lockDashboard('Session locked.'));
    document.getElementById('refreshUsers').addEventListener('click', () => fetchUsers());
    document.getElementById('pageSize').addEventListener('change', () => { userOffset = 0; fetchUsers(); });
    document.getElementById('userSearch').addEventListener('input', () => {
        clearTimeout(searchHandle); userOffset = 0; searchHandle = setTimeout(fetchUsers, 250);
    });
    document.getElementById('prevUsers').addEventListener('click', () => { const limit = Number(document.getElementById('pageSize').value || 25); userOffset = Math.max(0, userOffset - limit); fetchUsers(); });
    document.getElementById('nextUsers').addEventListener('click', () => { const limit = Number(document.getElementById('pageSize').value || 25); if (userOffset + limit < userTotal) userOffset += limit; fetchUsers(); });

    if (delegatedToken || masterPassword) authenticate();
    else document.getElementById('loginGate').classList.remove('hidden');
});

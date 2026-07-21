/**
 * Aircraft Alert Bot — Admin Dashboard Logic
 *
 * Fetches data from /admin/api/* endpoints and renders the dashboard.
 * Auto-refreshes every 30 seconds.
 */

const API_BASE = '/admin/api';
const REFRESH_INTERVAL = 30; // seconds
let countdown = REFRESH_INTERVAL;
let refreshTimer = null;

// ── Bootstrap ───────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    fetchAll();
    startRefreshTimer();
});

function startRefreshTimer() {
    countdown = REFRESH_INTERVAL;
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
        countdown--;
        const el = document.getElementById('refreshTimer');
        if (el) el.textContent = countdown + 's';
        if (countdown <= 0) {
            fetchAll();
            countdown = REFRESH_INTERVAL;
        }
    }, 1000);
}

// ── Fetch All Data ──────────────────────────────────────────────────────────

async function fetchAll() {
    await Promise.allSettled([
        fetchOverview(),
        fetchProviders(),
        fetchKeys(),
        fetchUsers(),
        fetchNotifications(),
        fetchSystem(),
    ]);
}

// ── API Helpers ─────────────────────────────────────────────────────────────

async function apiGet(endpoint) {
    try {
        const resp = await fetch(`${API_BASE}${endpoint}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        setStatus('online', 'Connected');
        return await resp.json();
    } catch (err) {
        setStatus('error', 'Error: ' + err.message);
        console.error(`API error (${endpoint}):`, err);
        return null;
    }
}

async function apiPost(endpoint) {
    try {
        const resp = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (err) {
        console.error(`API POST error (${endpoint}):`, err);
        return null;
    }
}

function setStatus(state, text) {
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if (dot) {
        dot.className = 'status-dot ' + state;
    }
    if (txt) txt.textContent = text;
}

// ── Formatters ──────────────────────────────────────────────────────────────

function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '-';
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

function formatTime(isoString) {
    if (!isoString || isoString === 'None') return '-';
    try {
        const d = new Date(isoString);
        return d.toLocaleString();
    } catch {
        return isoString;
    }
}

function formatTimestamp(epoch) {
    if (!epoch || epoch <= 0) return '-';
    return new Date(epoch * 1000).toLocaleTimeString();
}

function formatNumber(n) {
    if (n == null || n < 0) return '-';
    return n.toLocaleString();
}

// ── Overview ────────────────────────────────────────────────────────────────

async function fetchOverview() {
    const data = await apiGet('/overview');
    if (!data) return;

    setText('totalUsers', formatNumber(data.total_users));
    setText('activeUsers', formatNumber(data.active_users));
    setText('totalNotifications', formatNumber(data.total_notifications));
    setText('uptime', formatUptime(data.uptime_seconds));
    setText('memoryUsage', data.memory_mb > 0 ? `${data.memory_mb} MB` : 'N/A');

    const cycle = data.cycle_stats || {};
    if (cycle.last_cycle_time > 0) {
        const ago = Math.round((Date.now() / 1000) - cycle.last_cycle_time);
        setText('lastCycle', `${ago}s ago (${cycle.last_cycle_duration_ms}ms)`);
    } else {
        setText('lastCycle', 'No cycles yet');
    }
}

// ── Providers ───────────────────────────────────────────────────────────────

async function fetchProviders() {
    const data = await apiGet('/providers');
    if (!data) return;

    const tbody = document.getElementById('providersBody');
    if (!tbody) return;

    const providers = data.providers || [];
    if (providers.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No providers</td></tr>';
        return;
    }

    tbody.innerHTML = providers.map(p => {
        const typeLabel = p.is_unlimited
            ? '<span class="badge badge-success">Unlimited</span>'
            : '<span class="badge badge-warning">Credit-Limited</span>';

        const lastSuccess = p.last_success_time > 0
            ? formatTimestamp(p.last_success_time)
            : '-';

        const statusBadge = p.can_request_now
            ? '<span class="badge badge-success">Ready</span>'
            : '<span class="badge badge-muted">Rate Limited</span>';

        return `<tr>
            <td style="color:var(--text-primary);font-weight:500">${escHtml(p.name)}</td>
            <td>${typeLabel}</td>
            <td>${formatNumber(p.request_count)}</td>
            <td>${p.error_count > 0 ? '<span style="color:var(--danger)">' + p.error_count + '</span>' : '0'}</td>
            <td>${lastSuccess}</td>
            <td>${statusBadge}</td>
        </tr>`;
    }).join('');
}

// ── API Keys ────────────────────────────────────────────────────────────────

async function fetchKeys() {
    const data = await apiGet('/keys');
    if (!data) return;

    const tbody = document.getElementById('keysBody');
    if (!tbody) return;

    const keys = data.keys || [];
    if (keys.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading">No API keys configured</td></tr>';
        return;
    }

    tbody.innerHTML = keys.map(k => {
        const pct = Math.round((k.estimated_remaining / 4000) * 100);
        const progressClass = pct > 50 ? 'high' : pct > 20 ? 'medium' : 'low';

        let statusBadge;
        if (k.is_active && !k.is_exhausted) {
            statusBadge = '<span class="badge badge-success">Active</span>';
        } else if (k.is_exhausted) {
            statusBadge = '<span class="badge badge-danger">Exhausted</span>';
        } else {
            statusBadge = '<span class="badge badge-muted">Standby</span>';
        }

        return `<tr>
            <td>${k.index + 1}</td>
            <td style="color:var(--text-primary)">${escHtml(k.username)}</td>
            <td>${escHtml(k.source_file)}</td>
            <td>${formatNumber(k.requests_made)}</td>
            <td>
                ${formatNumber(k.estimated_remaining)}
                <div class="progress-bar">
                    <div class="progress-fill ${progressClass}" style="width:${pct}%"></div>
                </div>
            </td>
            <td>${k.rate_limit_hits > 0 ? '<span style="color:var(--warning)">' + k.rate_limit_hits + '</span>' : '0'}</td>
            <td>${statusBadge}</td>
        </tr>`;
    }).join('');
}

// ── Users ───────────────────────────────────────────────────────────────────

async function fetchUsers() {
    const data = await apiGet('/users');
    if (!data) return;

    const tbody = document.getElementById('usersBody');
    if (!tbody) return;

    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="loading">No users registered</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(u => {
        const loc = u.location || {};
        const prefs = u.preferences || {};
        const locStr = loc.latitude != null
            ? `${loc.latitude.toFixed(2)}, ${loc.longitude.toFixed(2)}`
            : 'Not set';

        const cats = (prefs.selected_categories || []);
        const catsHtml = cats.length > 0
            ? '<div class="chip-list">' + cats.map(c => `<span class="chip">${escHtml(c)}</span>`).join('') + '</div>'
            : '<span style="color:var(--text-muted)">None</span>';

        const custom = (prefs.custom_aircraft || []);
        const customHtml = custom.length > 0
            ? custom.map(c => `<code style="color:var(--accent-light)">${escHtml(c)}</code>`).join(', ')
            : '<span style="color:var(--text-muted)">None</span>';

        const statusBadge = u.setup_complete
            ? '<span class="badge badge-success">Active</span>'
            : '<span class="badge badge-muted">Inactive</span>';

        const toggleLabel = u.setup_complete ? 'Disable' : 'Enable';
        const toggleClass = u.setup_complete ? 'btn btn-danger' : 'btn';

        return `<tr>
            <td style="color:var(--text-primary)">${u.user_id}</td>
            <td>${escHtml(u.first_name || u.username || '-')}</td>
            <td>${locStr}</td>
            <td>${catsHtml}</td>
            <td>${customHtml}</td>
            <td>${statusBadge}</td>
            <td><button class="${toggleClass}" onclick="toggleUser(${u.user_id})">${toggleLabel}</button></td>
        </tr>`;
    }).join('');
}

// ── Notifications ───────────────────────────────────────────────────────────

async function fetchNotifications() {
    const data = await apiGet('/notifications?limit=30');
    if (!data) return;

    const tbody = document.getElementById('notificationsBody');
    if (!tbody) return;

    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="loading">No notifications yet</td></tr>';
        return;
    }

    tbody.innerHTML = data.map(n => {
        return `<tr>
            <td>${n.user_id}</td>
            <td style="color:var(--accent-light);font-family:monospace">${escHtml(n.aircraft_icao24)}</td>
            <td>${formatTime(n.notified_at)}</td>
            <td>${formatTime(n.cooldown_until)}</td>
        </tr>`;
    }).join('');
}

// ── System ──────────────────────────────────────────────────────────────────

async function fetchSystem() {
    const data = await apiGet('/system');
    if (!data) return;

    // Worker
    const worker = data.worker || {};
    setDetailRows('systemWorker', {
        'Total Cycles': formatNumber(worker.total_cycles),
        'Last Cycle': worker.last_cycle_duration_ms > 0 ? `${worker.last_cycle_duration_ms}ms` : '-',
    });

    // Database
    const db = data.database || {};
    if (db.error) {
        setText('systemDatabase', db.error);
    } else {
        setDetailRows('systemDatabase', {
            'Users': formatNumber(db.users),
            'Locations': formatNumber(db.locations),
            'Preferences': formatNumber(db.preferences),
            'Notifications': formatNumber(db.notifications),
            'Coverage Regions': formatNumber(db.coverage_regions),
        });
    }

    // Config
    const cfg = data.config || {};
    setDetailRows('systemConfig', {
        'Poll Interval': `${cfg.poll_interval_seconds}s`,
        'Default Radius': `${cfg.default_radius_km} km`,
        'Cooldown': `${cfg.cooldown_minutes} min`,
    });

    // Platform
    setDetailRows('systemPlatform', {
        'OS': data.platform || '-',
        'Python': data.python_version || '-',
        'Uptime': formatUptime(data.uptime_seconds),
    });
}

// ── User Actions ────────────────────────────────────────────────────────────

async function toggleUser(userId) {
    const result = await apiPost(`/user/${userId}/toggle`);
    if (result) {
        // Refresh users table
        await fetchUsers();
    }
}

// ── Utility ─────────────────────────────────────────────────────────────────

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setDetailRows(id, obj) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = Object.entries(obj).map(([label, value]) =>
        `<div class="detail-row">
            <span class="detail-label">${escHtml(label)}</span>
            <span class="detail-value">${escHtml(String(value))}</span>
        </div>`
    ).join('');
}

function escHtml(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

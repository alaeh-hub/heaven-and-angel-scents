// Notification bell, Halo widget, socket.io realtime updates, PH clock
// (Part of the app's main script; loaded in order by base.html.)

function initNotificationBell() {
    const wrap = document.getElementById('notifWrap');
    const toggle = document.getElementById('notifToggle');
    const panel = document.getElementById('notifPanel');
    const list = document.getElementById('notifList');
    const badge = document.getElementById('notifBadge');
    const clearBtn = document.getElementById('notifClear');
    if (!wrap || !toggle || !panel || !list || !badge) return null;

    const STORAGE_KEY = 'notifications:' + (wrap.dataset.user || 'anon');
    const MAX_ITEMS = 30;

    function load() {
        try {
            return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        } catch (e) {
            return [];
        }
    }

    function save(items) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
        } catch (e) {

        }
    }

    function timeAgo(ts) {
        const diffMins = Math.floor(Math.max(0, Date.now() - ts) / 60000);
        if (diffMins < 1) return 'just now';
        if (diffMins < 60) return diffMins + 'm ago';
        const diffHrs = Math.floor(diffMins / 60);
        if (diffHrs < 24) return diffHrs + 'h ago';
        return Math.floor(diffHrs / 24) + 'd ago';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function render() {
        const items = load();
        const unread = items.filter((n) => !n.read).length;

        if (unread > 0) {
            badge.textContent = unread > 9 ? '9+' : String(unread);
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }

        if (!items.length) {
            list.innerHTML = '<div class="notif-empty"><strong>You’re all caught up</strong><span>New requests, deliveries and low-stock alerts show up here.</span></div>';
            return;
        }

        list.innerHTML = items.map((n) => (
            '<div class="notif-item ' + (n.read ? '' : 'unread') + '">' +
            '<span class="notif-dot ' + (n.level || 'info') + '"></span>' +
            '<span class="notif-body">' + escapeHtml(n.message) +
            '<span class="notif-time">' + timeAgo(n.ts) + '</span>' +
            '</span>' +
            '</div>'
        )).join('');
    }

    function add(payload) {
        if (!payload || !payload.message) return;
        const items = load();
        items.unshift({ message: payload.message, level: payload.level || 'info', ts: Date.now(), read: false });
        save(items.slice(0, MAX_ITEMS));
        render();
        pulseBadge(badge);
        if (!PREFERS_REDUCED_MOTION.matches) {
            toggle.classList.remove('is-ringing');
            void toggle.offsetWidth;
            toggle.classList.add('is-ringing');
        }
    }

    function markAllRead() {
        save(load().map((n) => ({ ...n, read: true })));
        render();
    }

    function openPanel() {
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        toggle.setAttribute('aria-expanded', 'true');
        markAllRead();
    }

    function closePanel() {
        panel.classList.remove('open');
        panel.setAttribute('aria-hidden', 'true');
        toggle.setAttribute('aria-expanded', 'false');
    }

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (panel.classList.contains('open')) closePanel(); else openPanel();
    });

    document.addEventListener('click', (e) => {
        if (!wrap.contains(e.target)) closePanel();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePanel();
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            save([]);
            render();
        });
    }

    render();
    return { add };
}

// Halo, the floating AI assistant widget (markup + CSS in base.html). Its DOM lives
// outside .content, so this runs once per real page load and its state survives soft navs.
function initHaloWidget() {
    const widget = document.getElementById('haloWidget');
    const fab = document.getElementById('haloFabBtn');
    const panel = document.getElementById('haloPanel');
    const log = document.getElementById('haloLog');
    const form = document.getElementById('haloForm');
    const input = document.getElementById('haloInput');
    const sendBtn = document.getElementById('haloSend');
    if (!widget || !fab || !panel || !log || !form || !input || !sendBtn) return;

    const csrfMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfMeta ? csrfMeta.content : '';
    const history = [];
    let empty = document.getElementById('haloEmpty');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // Same light markdown subset as ai/chat.html; escaped first, so no HTML injection.
    function formatAssistantText(text) {
        let escaped = escapeHtml(text);
        escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        escaped = escaped.replace(/(^|\s)\*([^*\s][^*]*?)\*(?=\s|$)/g, '$1<em>$2</em>');
        escaped = escaped.replace(/\n/g, '<br>');
        return escaped;
    }

    function isOpen() {
        return widget.classList.contains('open');
    }

    function openPanel() {
        widget.classList.add('open');
        widget.classList.remove('has-unread');
        panel.hidden = false;
        panel.classList.add('opening');
        window.setTimeout(() => panel.classList.remove('opening'), 200);
        fab.setAttribute('aria-expanded', 'true');
        input.focus();
        log.scrollTop = log.scrollHeight;
    }

    function closePanel() {
        widget.classList.remove('open');
        panel.hidden = true;
        fab.setAttribute('aria-expanded', 'false');
    }

    fab.addEventListener('click', () => {
        if (isOpen()) closePanel(); else openPanel();
    });

    // On phones the panel is full screen and the floating button is hidden, so this closes it.
    const closeBtn = document.getElementById('haloClose');
    if (closeBtn) closeBtn.addEventListener('click', closePanel);

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen()) closePanel();
    });

    function addMessage(role, text) {
        if (empty) { empty.remove(); empty = null; }

        const row = document.createElement('div');
        row.className = 'halo-row ' + role;

        const bubble = document.createElement('div');
        bubble.className = 'halo-msg';
        if (role === 'user') {
            bubble.textContent = text;
        } else {
            bubble.innerHTML = formatAssistantText(text);
        }
        row.appendChild(bubble);
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;

        if (window.Motion && !reduceMotion) {
            const anim = window.Motion.animate(
                row,
                { opacity: [0, 1], transform: ['translateY(6px)', 'translateY(0px)'] },
                { duration: 0.22, easing: [0.16, 1, 0.3, 1] }
            );
            if (anim && typeof anim.then === 'function') anim.then(null, () => { });
        }

        // Only for an assistant/error reply that arrives after the panel was closed mid-request.
        if (role !== 'user' && !isOpen()) {
            widget.classList.add('has-unread');
        }

        return row;
    }

    function addTypingIndicator() {
        if (empty) { empty.remove(); empty = null; }
        const row = document.createElement('div');
        row.className = 'halo-row assistant';
        row.innerHTML = '<div class="halo-msg"><div class="halo-typing-dots"><span></span><span></span><span></span></div></div>';
        log.appendChild(row);
        log.scrollTop = log.scrollHeight;
        return row;
    }

    function sendMessage(message) {
        if (!message) return;

        addMessage('user', message);
        const sentHistory = history.slice();
        history.push({ role: 'user', text: message });

        input.value = '';
        input.style.height = 'auto';
        input.disabled = true;
        sendBtn.disabled = true;

        const thinking = addTypingIndicator();

        fetch('/ai/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ message: message, history: sentHistory })
        })
            .then((r) => r.json().then((data) => ({ ok: r.ok, data: data })))
            .then((res) => {
                thinking.remove();
                if (res.ok && res.data.reply) {
                    addMessage('assistant', res.data.reply);
                    history.push({ role: 'model', text: res.data.reply });
                } else {
                    addMessage('error', res.data.error || 'Something went wrong.');
                }
            })
            .catch(() => {
                thinking.remove();
                addMessage('error', 'Could not reach Halo. Check your connection and try again.');
            })
            .finally(() => {
                input.disabled = false;
                sendBtn.disabled = false;
                if (isOpen()) input.focus();
            });
    }

    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 100) + 'px';
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        sendMessage(input.value.trim());
    });

    if (empty) {
        empty.addEventListener('click', (e) => {
            const btn = e.target.closest('.halo-suggestion');
            if (!btn) return;
            sendMessage(btn.dataset.q);
        });
    }
}

function initRealtime(notifBell) {
    if (typeof io === 'undefined') return;

    const ROUTE_SCOPES = [
        { match: /^\/admin\/production\/?$/, scopes: ['production', 'inventory', 'movement_logs'] },
        { match: /^\/admin\/requests\/?$/, scopes: ['requests'] },
        { match: /^\/admin\/branch-stock\/?$/, scopes: ['inventory'] },
        { match: /^\/admin\/movement-logs\/?$/, scopes: ['movement_logs'] },
        { match: /^\/admin\/products\/?$/, scopes: ['products'] },
        { match: /^\/admin\/branches\/?$/, scopes: ['branches'] },
        { match: /^\/admin\/partners\/?$/, scopes: ['partners'] },
        { match: /^\/admin\/packages(\/\d+)?\/?$/, scopes: ['packages'] },
        { match: /^\/admin\/partners\/inquiries\/?$/, scopes: ['partner_inquiries'] },
        { match: /^\/admin\/users\/?$/, scopes: ['users'] },
        { match: /^\/admin\/(materials|suppliers|formulas|tools)\/?$/, scopes: ['materials'] },
        { match: /^\/admin\/bulk-batches\/?$/, scopes: ['materials', 'production'] },
        // Process Flow shows material prices/stock, batch and base cost, so it follows the same changes.
        { match: /^\/admin\/process-flow\/?$/, scopes: ['materials', 'production'] },
        { match: /^\/admin\/partners\/\d+\/?$/, scopes: ['partners'] },
        { match: /^\/admin\/requests\/\d+\/?$/, scopes: ['requests'] },
        { match: /^\/admin\/(low-stock|discrepancies)\/?$/, scopes: ['inventory', 'movement_logs'] },
        { match: /^\/admin\/(sales-history|customers|credit-purchases|record-sale)\/?$/, scopes: ['sales'] },
        { match: /^\/ai\/drafts\/?$/, scopes: ['ai_drafts'] },
        { match: /^\/admin\/?$/, scopes: ['requests', 'inventory', 'movement_logs', 'production', 'sales', 'partner_inquiries', 'materials'] },
        { match: /^\/admin\/reports\/?$/, scopes: ['sales', 'inventory', 'movement_logs', 'production', 'materials', 'partner_inquiries', 'branches'] },
        { match: /^\/admin\/branch-performance\/?$/, scopes: ['sales', 'inventory', 'movement_logs', 'branches'] },
        { match: /^\/branch\/reports\/?$/, scopes: ['sales', 'inventory', 'movement_logs'] },
        { match: /^\/branch\/inventory\/?$/, scopes: ['inventory'] },
        { match: /^\/branch\/request-stock\/?$/, scopes: ['requests'] },
        { match: /^\/branch\/receive-stock\/?$/, scopes: ['requests', 'inventory'] },
        { match: /^\/branch\/record-sale\/?$/, scopes: ['inventory', 'sales'] },
        { match: /^\/branch\/sales-history\/?$/, scopes: ['sales'] },
        { match: /^\/branch\/?$/, scopes: ['requests', 'inventory', 'sales'] },
    ];

    function currentScopes() {
        const path = window.location.pathname;
        const hit = ROUTE_SCOPES.find((r) => r.match.test(path));
        return hit ? hit.scopes : [];
    }

    // Search boxes and GET filter forms never block a live refresh.
    function isDataField(el) {
        if (!el || !el.closest) return false;
        if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.tagName !== 'SELECT') return false;
        if (el.type === 'search' || el.type === 'hidden' || el.type === 'button' || el.type === 'submit') return false;
        if (!el.closest('.content')) return false;
        // Only fields inside a POST form are protected; search boxes and filters are not.
        const form = el.closest('form');
        if (!form || (form.getAttribute('method') || 'get').toLowerCase() === 'get') return false;
        return true;
    }

    // Table search box and filters are carried across the swap, not reset.
    const VIEW_CONTROLS = '.smart-search-input, .smart-filter-select';

    function snapshotViewControls() {
        return Array.from(document.querySelectorAll(`.content ${VIEW_CONTROLS}`)).map((el) => el.value);
    }

    function restoreViewControls(values) {
        const els = document.querySelectorAll(`.content ${VIEW_CONTROLS}`);
        els.forEach((el, i) => {
            if (values[i] === undefined || el.value === values[i]) return;
            el.value = values[i];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }

    function activeFieldInsideContent() {
        return isDataField(document.activeElement);
    }

    let pendingRefresh = false;
    let suppressUntil = 0;
    let retryTimer = null;

    // True if the user typed/selected anything in .content that a refresh would discard.
    // Not focus-based: focus is briefly "nowhere" while tabbing between fields.
    // Self-clearing: counts only while the value differs from the server render.
    const touchedFields = new WeakSet();

    function markTouched(e) {
        if (isDataField(e.target)) touchedFields.add(e.target);
    }
    document.addEventListener('input', markTouched, true);
    document.addEventListener('change', markTouched, true);

    function fieldDiffers(el) {
        if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== el.defaultChecked;
        if (el.tagName === 'SELECT') return Array.from(el.options).some((o) => o.selected !== o.defaultSelected);
        return el.value !== el.defaultValue;
    }

    // Forms in .content the user has actually changed.
    function dirtyForms() {
        const content = document.querySelector('.content');
        if (!content) return [];
        const forms = new Set();
        content.querySelectorAll('input, textarea, select').forEach((el) => {
            if (touchedFields.has(el) && isDataField(el) && fieldDiffers(el)) forms.add(el.closest('form'));
        });
        return Array.from(forms);
    }

    function hasUnsavedInput() {
        return dirtyForms().length > 0;
    }

    document.addEventListener('submit', (e) => {
        if (e.target && e.target.closest && e.target.closest('.content')) {
            suppressUntil = Date.now() + 2500;
        }
    }, true);

    // Coalesce a burst of sales events into one refresh after things go quiet.
    let queuedRefresh = null;
    function queueSoftRefresh() {
        clearTimeout(queuedRefresh);
        queuedRefresh = setTimeout(softRefresh, 1500);
    }

    function scheduleRetry(ms) {
        clearTimeout(retryTimer);
        retryTimer = setTimeout(softRefresh, ms);
    }

    // Hold the refresh while a dialog inside .content is open (the swap would close it and
    // lose input). Covers role="dialog" and page overlays (.pg-overlay, .pi-modal-overlay,
    // .void-overlay, .temp-overlay, ...): any shown fixed-position overlay in .content.
    const DIALOG_SELECTOR = '.content [role="dialog"], .content [class*="overlay"], .content [class*="modal"]';

    function hasOpenDialog() {
        return Array.from(document.querySelectorAll(DIALOG_SELECTOR)).some((el) => {
            if (el.hidden) return false;
            const cs = getComputedStyle(el);
            return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.position === 'fixed';
        });
    }

    let lastSig = null;
    let lastSigUrl = null;

    function softRefresh() {
        if (hasOpenDialog()) {
            pendingRefresh = true;
            scheduleRetry(3000);
            return;
        }
        const wait = suppressUntil - Date.now();
        if (wait > 0) {
            // Our own submit just happened; don't drop the update, replay it after.
            pendingRefresh = true;
            scheduleRetry(wait + 100);
            return;
        }
        pendingRefresh = false;

        const viewValues = snapshotViewControls();
        fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html');

                // Same content as the last refresh: skip the swap (it would replay the entrance
                // animation). Sidebar/topbar are outside .content, so keep those current.
                const freshContent = fresh.querySelector('.content');
                const sig = freshContent ? freshContent.innerHTML : null;
                if (sig !== null && lastSigUrl === window.location.href && sig === lastSig) {
                    patchSidebarNav(fresh);
                    patchTopbar(fresh);
                    return;
                }
                lastSig = sig;
                lastSigUrl = window.location.href;

                // A half-filled form is carried over as-is (same node, so values and listeners survive).
                // If it can't be matched in the new page, hold the refresh and retry.
                const keep = [];
                for (const form of dirtyForms()) {
                    const freshForm = form.id ? fresh.getElementById(form.id) : null;
                    if (!freshForm) {
                        console.debug('[realtime] refresh deferred: unsaved form cannot be preserved');
                        pendingRefresh = true;
                        scheduleRetry(3000);
                        return;
                    }
                    keep.push([form, freshForm]);
                }
                keep.forEach(([form, freshForm]) => freshForm.replaceWith(form));

                swapContent(fresh);
                restoreViewControls(viewValues);
            })
            .catch(() => {
                // Transient failure (network blip, restart): try again shortly.
                pendingRefresh = true;
                scheduleRetry(5000);
            });
    }

    // Safety net: pushes are lost while the socket is down, so catch up on reconnect.
    // Not on every tab return (each refresh replays the reveal like a reload); only after
    // a long absence or a dropped socket.
    const STALE_AFTER_HIDDEN_MS = 2 * 60 * 1000;
    let hiddenAt = 0;
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            hiddenAt = Date.now();
            return;
        }
        const away = hiddenAt ? Date.now() - hiddenAt : 0;
        hiddenAt = 0;
        const socketDown = typeof socket !== 'undefined' && !socket.connected;
        if (away >= STALE_AFTER_HIDDEN_MS || socketDown) softRefresh();
    });

    function refreshBadgesOnly() {

        fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
            .then((html) => {
                const fresh = new DOMParser().parseFromString(html, 'text/html');
                patchSidebarNav(fresh);
            })
            .catch(() => { });
    }

    document.addEventListener('focusout', () => {
        // Deferred a tick: at focusout, document.activeElement hasn't settled yet, so a
        // synchronous check could treat an in-form focus move as leaving the form.
        setTimeout(() => {
            if (pendingRefresh && !activeFieldInsideContent() && !hasUnsavedInput()) {
                softRefresh();
            }
        }, 0);
    }, true);

    const socket = io();
    // Pushes sent while disconnected are gone for good; refetch on reconnect.
    let hasConnected = false;
    socket.on('connect', () => {
        console.debug('[realtime] connected', socket.id);
        if (hasConnected) softRefresh();
        hasConnected = true;
    });
    socket.on('disconnect', (reason) => console.debug('[realtime] disconnected:', reason));
    socket.on('connect_error', (err) => console.debug('[realtime] connect_error:', err && err.message));
    socket.on('data_changed', (payload) => {
        const scopes = (payload && payload.scopes) || [];
        if (scopes.includes('sales')) refreshTodaySales();
        const mine = currentScopes();
        console.debug('[realtime] data_changed', scopes, 'page listens for', mine);
        if (scopes.some((s) => mine.includes(s))) {
            // A page can register its own in-place refresh (Reports keeps its view and builder form).
            const live = window.__liveRefresh;
            if (live && live.path === window.location.pathname && typeof live.fn === 'function') {
                live.fn();
            } else {
                queueSoftRefresh();
            }
        } else if (scopes.includes('requests') || scopes.includes('partner_inquiries') || scopes.includes('ai_drafts')) {
            refreshBadgesOnly();
        }
    });
    socket.on('bell_notification', (payload) => {
        if (notifBell) notifBell.add(payload);
        if (payload) showToast(payload.message, payload.level);
    });
}

function initPhClock() {
    const el = document.getElementById('phClock');
    if (!el) return;

    const formatter = new Intl.DateTimeFormat('en-PH', {
        timeZone: 'Asia/Manila',
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
    });

    // One span per character; only changed characters re-render (short slide-in) each second.
    let prev = '';
    let cells = [];

    function tick() {
        const next = formatter.format(new Date());
        if (next.length !== prev.length) {
            el.textContent = '';
            cells = Array.from(next).map((ch) => {
                const span = document.createElement('span');
                span.className = 'clock-char';
                span.textContent = ch;
                el.appendChild(span);
                return span;
            });
        } else {
            const animate = !PREFERS_REDUCED_MOTION.matches && document.visibilityState === 'visible';
            Array.from(next).forEach((ch, i) => {
                if (ch === prev[i]) return;
                const old = cells[i];
                const span = document.createElement('span');
                span.className = 'clock-char';
                span.textContent = ch;
                if (animate && /\d/.test(ch)) span.classList.add('clock-tick');
                old.replaceWith(span);
                cells[i] = span;
            });
        }
        prev = next;
    }

    tick();
    setInterval(tick, 1000);
}
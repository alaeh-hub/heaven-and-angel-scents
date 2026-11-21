// Flash messages, badges, sidebar/topbar patching, today's-sales chip
// (Part of the app's main script; loaded in order by base.html.)

// Fades a flash/toast out and removes it after 5s; shared with soft-nav's appendFreshFlashes().
function attachFlashDismiss(el) {
    setTimeout(() => {
        el.classList.add('flash-exit');
        el.addEventListener('animationend', () => el.remove(), { once: true });
    }, 5000);
}

// Scale bounce on a badge whose count rose. Uses the CSS `scale` property so it composes
// with the badge's positioning transform; plain WAAPI with no fill leaves no inline style
// (a Motion version pinned translateY(-50%) inline and misplaced the collapsed-rail badge).
function pulseBadge(el) {
    if (!el || typeof el.animate !== 'function'
        || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    el.animate(
        { scale: ['1', '1.35', '1'] },
        { duration: 400, easing: 'cubic-bezier(0.34, 1.56, 0.64, 1)' }
    );
}

// Copies sidebar badge counts and the active link from a fetched document onto the live
// sidebar (only .content is swapped). Links are matched by href.
function patchSidebarNav(freshDoc) {
    document.querySelectorAll('.nav-link[href]').forEach((link) => {
        const href = link.getAttribute('href');
        // A group parent and its first page share an href; keep them apart.
        const kind = link.classList.contains('nav-sub') ? '.nav-sub' : ':not(.nav-sub)';
        const freshLink = freshDoc.querySelector(`.nav-link${kind}[href="${href}"]`);
        if (!freshLink) return;

        const liveBadge = link.querySelector('.nav-badge');
        const freshBadge = freshLink.querySelector('.nav-badge');
        if (liveBadge && !freshBadge) {
            liveBadge.remove();
        } else if (freshBadge && !liveBadge) {
            const added = freshBadge.cloneNode(true);
            link.appendChild(added);
            pulseBadge(added);
        } else if (freshBadge && liveBadge) {
            const before = parseInt(liveBadge.textContent, 10) || 0;
            const after = parseInt(freshBadge.textContent, 10) || 0;
            liveBadge.textContent = freshBadge.textContent;
            if (after > before) pulseBadge(liveBadge);
        }

        link.classList.toggle('active', freshLink.classList.contains('active'));
        // Same for the screen-reader side: the fresh page says which link is current.
        const current = freshLink.getAttribute('aria-current');
        if (current) link.setAttribute('aria-current', current);
        else link.removeAttribute('aria-current');
    });
    // Phone bottom tab bar (mobile.css): follow the fresh page's current tab.
    document.querySelectorAll('.m-tab[href]').forEach((tab) => {
        const freshTab = freshDoc.querySelector(`.m-tab[href="${tab.getAttribute('href')}"]`);
        if (!freshTab) return;
        tab.classList.toggle('active', freshTab.classList.contains('active'));
        if (freshTab.getAttribute('aria-current')) tab.setAttribute('aria-current', 'page');
        else tab.removeAttribute('aria-current');
    });
    syncSidebarIndicator();
}

// Copies the topbar eyebrow + heading ({% block eyebrow/heading %}) from a fetched
// document onto the live page; the topbar is outside .content, so swapContent() misses it.
function patchTopbar(freshDoc) {
    const liveEyebrow = document.querySelector('.topbar .eyebrow');
    const freshEyebrow = freshDoc.querySelector('.topbar .eyebrow');
    if (liveEyebrow && freshEyebrow) liveEyebrow.textContent = freshEyebrow.textContent;

    const liveHeading = document.querySelector('.topbar h1');
    const freshHeading = freshDoc.querySelector('.topbar h1');
    if (liveHeading && freshHeading) liveHeading.textContent = freshHeading.textContent;

    // The chip keeps its live odometer DOM; only its values are updated.
    const freshChip = freshDoc.getElementById('todaySalesChip');
    if (freshChip && freshChip.dataset.summary) {
        try { applyTodaySales(JSON.parse(freshChip.dataset.summary)); } catch (e) { }
    }
}

const PREFERS_REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

// Gold hairline on the top bar's edge during a soft navigation; delayed so instant
// loads don't flicker.
const navProgress = (() => {
    let showTimer = null;
    let shown = false;

    function el() { return document.getElementById('topbarProgress'); }

    function start() {
        const bar = el();
        if (!bar) return;
        clearTimeout(showTimer);
        showTimer = setTimeout(() => {
            shown = true;
            bar.classList.remove('is-done');
            bar.style.removeProperty('--from');
            void bar.offsetWidth;
            bar.classList.add('is-loading');
        }, 120);
    }

    function done() {
        const bar = el();
        clearTimeout(showTimer);
        if (!bar || !shown) return;
        shown = false;
        const m = /matrix\(([^,]+)/.exec(getComputedStyle(bar).transform);
        bar.style.setProperty('--from', m ? m[1] : '0.3');
        bar.classList.remove('is-loading');
        void bar.offsetWidth;
        bar.classList.add('is-done');
    }

    return { start, done };
})();

// Top bar entrance on a full page load only, plus the "scrolled underneath" shadow via
// an IntersectionObserver on a marker above the sticky bar.
function initTopbarMotion() {
    const bar = document.getElementById('topbar');
    if (!bar) return;

    if (!PREFERS_REDUCED_MOTION.matches) {
        const items = bar.querySelectorAll('.topbar-left > :not(.topbar-title), .topbar-right > *');
        items.forEach((item, i) => item.style.setProperty('--tb-i', String(i)));
        bar.classList.add('is-entering');
        setTimeout(() => bar.classList.remove('is-entering'), 900 + items.length * 50);
    }

    const sentinel = document.getElementById('topbarSentinel');
    if (sentinel && typeof IntersectionObserver === 'function') {
        new IntersectionObserver(([entry]) => {
            bar.classList.toggle('is-scrolled', !entry.isIntersecting);
        }).observe(sentinel);
    }
}

// Odometer: each digit is a 0-9 column sliding to its value; transform-only, changed digits only.
function setOdometer(el, text, opts) {
    if (!el) return;
    text = String(text);
    const animate = !(opts && opts.instant) && !PREFERS_REDUCED_MOTION.matches;
    const prev = el.dataset.odoText;
    if (prev === text) return;

    const sameShape = prev !== undefined && prev.length === text.length &&
        prev.replace(/\d/g, '0') === text.replace(/\d/g, '0');

    if (!sameShape) {
        el.textContent = '';
        el.classList.add('odo');
        Array.from(text).forEach((ch, i) => {
            if (/\d/.test(ch)) {
                const cell = document.createElement('span');
                cell.className = 'odo-cell';
                const strip = document.createElement('span');
                strip.className = 'odo-strip';
                for (let d = 0; d <= 9; d++) {
                    const n = document.createElement('span');
                    n.textContent = d;
                    strip.appendChild(n);
                }
                cell.appendChild(strip);
                // A new shape starts every digit at 0 and rolls up to its value.
                strip.style.transform = 'translateY(0)';
                cell.style.setProperty('--odo-i', String(text.length - i));
                el.appendChild(cell);
            } else {
                const sym = document.createElement('span');
                sym.className = 'odo-sym';
                sym.textContent = ch;
                el.appendChild(sym);
            }
        });
        if (animate) void el.offsetWidth;
    }

    const strips = el.querySelectorAll('.odo-strip');
    let s = 0;
    el.classList.toggle('odo-animate', animate);
    Array.from(text).forEach((ch) => {
        if (!/\d/.test(ch)) return;
        strips[s++].style.transform = `translateY(${-Number(ch) * 10}%)`;
    });
    el.dataset.odoText = text;
}

// Top bar "Today" chip: re-fetched (small JSON) on any visible sale/void, on tab
// return, and every few minutes so it rolls over at midnight.
let todaySalesInFlight = false;

function refreshTodaySales() {
    if (todaySalesInFlight || !document.getElementById('todaySalesChip')) return;
    todaySalesInFlight = true;
    fetch('/api/today-sales', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((data) => applyTodaySales(data))
        .catch(() => { })
        .finally(() => { todaySalesInFlight = false; });
}

function applyTodaySales(data, opts) {
    const chip = document.getElementById('todaySalesChip');
    if (!chip || !data) return;
    const instant = !!(opts && opts.instant);
    const last = chip._todayLast;
    const changed = !last || last.revenue !== data.revenue || last.count !== data.count;

    setOdometer(chip.querySelector('[data-today-odo="revenue_display"]'), data.revenue_display, { instant });
    setOdometer(chip.querySelector('[data-today-odo="revenue_compact"]'), data.revenue_compact, { instant });
    setOdometer(chip.querySelector('[data-today-odo="count"]'), data.count, { instant });
    const word = chip.querySelector('[data-today-field="count_word"]');
    if (word) word.textContent = data.count === 1 ? 'sale' : 'sales';

    const trendEl = chip.querySelector('[data-today-field="trend"]');
    if (trendEl) {
        const t = data.trend;
        const wasHidden = trendEl.hidden;
        trendEl.hidden = t === null || t === undefined;
        if (!trendEl.hidden) {
            trendEl.className = 'today-chip-trend ' + (t > 0 ? 'up' : t < 0 ? 'down' : 'flat');
            trendEl.textContent = (t > 0 ? '▲ ' : t < 0 ? '▼ ' : '• ') + Math.abs(t) + '%';
            if (wasHidden && !instant) trendEl.classList.add('is-new');
        }
    }

    const countLabel = data.count + ' sale' + (data.count === 1 ? '' : 's');
    chip.title = "Today's sales · " + data.scope;
    chip.setAttribute('aria-label', "Today's sales for " + data.scope + ': ' + data.revenue_display +
        ' from ' + countLabel);
    chip.dataset.summary = JSON.stringify(data);
    chip._todayLast = { revenue: data.revenue, count: data.count };

    if (last && changed && !instant && !PREFERS_REDUCED_MOTION.matches) {
        chip.classList.remove('is-updated');
        void chip.offsetWidth;
        chip.classList.add('is-updated');
    }
}

function initTodaySalesChip() {
    const chip = document.getElementById('todaySalesChip');
    if (!chip) return;
    chip.addEventListener('animationend', (e) => {
        if (e.target === chip) chip.classList.remove('is-updated');
    });

    // First paint: odometers at zero, then roll up to today's figures after the bar enters.
    let data = null;
    try { data = JSON.parse(chip.dataset.summary); } catch (e) { }
    if (data) {
        if (PREFERS_REDUCED_MOTION.matches) {
            applyTodaySales(data, { instant: true });
        } else {
            const zeroed = Object.assign({}, data, {
                revenue_display: data.revenue_display.replace(/\d/g, '0'),
                revenue_compact: data.revenue_compact.replace(/\d/g, '0'),
                count: Number(String(data.count).replace(/\d/g, '0')),
            });
            applyTodaySales(zeroed, { instant: true });
            chip._todayLast = null;
            setTimeout(() => {
                applyTodaySales(data);
                chip._todayLast = { revenue: data.revenue, count: data.count };
            }, 260);
        }
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') refreshTodaySales();
    });
    setInterval(() => {
        if (document.visibilityState === 'visible') refreshTodaySales();
    }, 5 * 60 * 1000);
}


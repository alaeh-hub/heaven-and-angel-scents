// CSRF helper, same-page link check, sidebar scroll/gutter/title helpers
// (Part of the app's main script; loaded in order by base.html.)

// Shared CSRF token reader; the meta tag is rendered once in base.html.
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

// True when `link` is exactly the current page (same path + query, no #hash): a no-op click.
function isSamePageNoOpLink(link) {
    let url;
    try {
        url = new URL(link.href, window.location.href);
    } catch (e) {
        return false;
    }
    return url.pathname === window.location.pathname &&
        url.search === window.location.search &&
        !url.hash;
}

(function initSidebarScrollMemory() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    const STORAGE_KEY = 'sidebarScrollTop';

    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (saved !== null) {
        sidebar.scrollTop = parseInt(saved, 10) || 0;
    }

    let saveTimer = null;
    sidebar.addEventListener('scroll', () => {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
            sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
        }, 80);
    });

    sidebar.addEventListener('click', (e) => {
        if (e.target.closest && e.target.closest('a')) {
            sessionStorage.setItem(STORAGE_KEY, String(sidebar.scrollTop));
        }
    });
})();

// Measure the sidebar scrollbar gutter into --sidebar-gutter (CSS can't know its width).
// Runs now (before first paint) and on resize/zoom.
(function initSidebarGutter() {
    const sidebar = document.getElementById('sidebar');
    const brand = sidebar && sidebar.querySelector('.brand');
    // base.html adds html.sidebar-booting (transitions frozen); lift it after first measure.
    const settle = () => requestAnimationFrame(() => requestAnimationFrame(
        () => document.documentElement.classList.remove('sidebar-booting')));
    if (!brand) { settle(); return; }

    function measure() {
        const cs = getComputedStyle(sidebar);
        // Gutter = space the scroll box gives up beside its content.
        const gutter = sidebar.offsetWidth - sidebar.clientWidth
            - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
        if (gutter >= 0) {
            document.documentElement.style.setProperty('--sidebar-gutter', gutter + 'px');
            try { localStorage.setItem('sidebarGutter', String(gutter)); } catch (e) { }
        }
    }

    measure();
    settle();
    window.addEventListener('resize', measure);
})();

// Truncated table cells get a hover title with their full text (delegated, so new rows work).
(function initTruncatedCellTitles() {
    document.addEventListener('mouseover', (e) => {
        const cell = e.target.closest && e.target.closest('table.table-fixed td, table.table-fixed th');
        if (!cell) return;
        for (let el = e.target; el && el !== cell.parentElement; el = el.parentElement) {
            const own = el.hasAttribute('title') && !el.dataset.autoTitle;
            if (own) return;
            const truncated = el.scrollWidth > el.clientWidth + 1;
            if (truncated) {
                el.title = el.textContent.replace(/\s+/g, ' ').trim();
                el.dataset.autoTitle = '1';
                return;
            }
            if (el.dataset.autoTitle) {
                el.removeAttribute('title');
                delete el.dataset.autoTitle;
            }
        }
    });
})();


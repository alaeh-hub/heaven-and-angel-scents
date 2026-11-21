// Soft navigation: swap page content without a full reload
// (Part of the app's main script; loaded in order by base.html.)

// Copies a page's own {% block head %} (between <!--page-head-start/end--> markers in
// base.html) from a fetched document into the live <head>. Pages put layout-critical
// <style> there (segmented controls, .scan-grid, .chat-shell, ...), which a .content
// swap never touches, so soft-navigated pages would render unstyled. Run on every swap.
function patchPageHead(freshDoc) {
    function markers(doc) {
        let start = null;
        let end = null;
        doc.head.childNodes.forEach((node) => {
            if (node.nodeType !== Node.COMMENT_NODE) return;
            const text = node.data.trim();
            if (text === 'page-head-start') start = node;
            if (text === 'page-head-end') end = node;
        });
        return { start, end };
    }

    const live = markers(document);
    const fresh = markers(freshDoc);
    if (!live.start || !live.end || !fresh.start || !fresh.end) return;

    while (live.start.nextSibling && live.start.nextSibling !== live.end) {
        live.start.nextSibling.remove();
    }

    let node = fresh.start.nextSibling;
    while (node && node !== fresh.end) {
        document.head.insertBefore(document.importNode(node, true), live.end);
        node = node.nextSibling;
    }
}

// Re-runs everything that makes freshly swapped-in .content interactive; shared by
// softRefresh() (realtime) and initSoftNav() (link/form navigation).
function refreshDynamicContent(skipReveal) {
    safeInit(initFillBars, 'fillBars');
    safeInit(initSmartTables, 'smartTables');
    safeInit(initSmartLists, 'smartLists');
    safeInit(initDispatchQtyWarnings, 'dispatchQtyWarnings');
    safeInit(initCustomSelects, 'customSelects');
    safeInit(initDatePickers, 'datePickers');
    // initConfirmDialogs() is not re-called: it is delegated on `document` once.
    // skipReveal (same-path tab/filter soft nav, see go()'s preserveScroll) turns off the
    // content reveal and stat count-up: re-animating every number on a tab click feels
    // like a reload, and the swapped-in markup is already correct.
    if (!skipReveal) {
        safeInit(revealContent, 'revealContent');
        // On phones the page fade is the whole entrance; count-up/stagger/reveal made tabs lag.
        if (!window.matchMedia('(max-width: 760px)').matches) {
            safeInit(initStatCountUp, 'statCountUp');
            safeInit(initEntranceStagger, 'entranceStagger');
            safeInit(initBelowFoldReveal, 'belowFoldReveal');
        }
    }
}

// Replaces the live .content with freshDoc's .content. Returns false (touching nothing)
// when there is none (e.g. the login page after a session expires); callers then fall
// back to a real navigation.
function swapContent(freshDoc, skipReveal) {
    const freshContent = freshDoc.querySelector('.content');
    const liveContent = document.querySelector('.content');
    if (!freshContent || !liveContent) return false;
    liveContent.replaceWith(freshContent);
    if (freshDoc.title) document.title = freshDoc.title;
    patchSidebarNav(freshDoc);
    patchTopbar(freshDoc);
    patchPageHead(freshDoc);
    refreshDynamicContent(skipReveal);
    // Deferred two animation frames: scripts that measure layout on creation (every
    // Chart.js instance) can catch the browser mid-reflow right after the swap and come
    // out squashed. The second rAF runs after the new layout has painted.
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            runPageScripts(freshDoc);
        });
    });
    return true;
}

// Re-runs the page's {% block scripts %} (#page-scripts, outside .content) after each
// swap: DOMParser never executes fetched scripts, so without this pages whose behavior
// lives there (charts, Record Sale switches, modals) stay inert until a real reload.
function runPageScripts(freshDoc) {
    const freshScripts = freshDoc.getElementById('page-scripts');
    const liveScripts = document.getElementById('page-scripts');
    if (!freshScripts || !liveScripts) return;

    liveScripts.innerHTML = '';

    const oldScripts = Array.prototype.slice.call(freshScripts.querySelectorAll('script'));

    // Run page scripts strictly one at a time, in document order. A dynamically inserted
    // inline script runs immediately (async=false only orders src scripts), which let a
    // chart init run before Chart.js loaded and silently draw nothing. Chain each script
    // off the previous src script's load/error event to restore real page-load ordering.
    function runNext(index) {
        if (index >= oldScripts.length) return;
        const oldScript = oldScripts[index];
        const newScript = document.createElement('script');
        Array.prototype.forEach.call(oldScript.attributes, (attr) => {
            newScript.setAttribute(attr.name, attr.value);
        });
        // The fetched page has its own CSP nonce the live page doesn't know; stamp this page's
        // nonce (from <meta name="csp-nonce">).
        const liveNonce = document.querySelector('meta[name="csp-nonce"]');
        if (liveNonce) newScript.nonce = liveNonce.content;

        if (oldScript.src) {
            newScript.onload = () => runNext(index + 1);
            newScript.onerror = () => runNext(index + 1);
            liveScripts.appendChild(newScript);
            return;
        }

        // Inline script: wrapped in its own IIFE so top-level const/let don't collide
        // ("already declared") when the same page is revisited without a reload.
        newScript.textContent = '(function () {\n' + oldScript.textContent + '\n})();';
        liveScripts.appendChild(newScript);
        runNext(index + 1);
    }

    runNext(0);
}

// .flashes lives outside .content so swaps don't wipe it, but that means a soft-submitted
// form's queued flash ("Product added.") isn't surfaced; this clones those into the live
// toast stack and starts their dismiss timer.
function getFlashContainer() {
    let liveContainer = document.querySelector('.flashes');
    if (!liveContainer) {
        liveContainer = document.createElement('div');
        liveContainer.className = 'flashes';
        liveContainer.setAttribute('role', 'status');
        liveContainer.setAttribute('aria-live', 'polite');
        document.body.appendChild(liveContainer);
    }
    return liveContainer;
}

// Pops a client-side toast in the server-flash stack (live bell notifications).
// level: "info" | "success" | "warning". textContent only, since messages carry user text.
function showToast(message, level) {
    if (!message) return;
    const el = document.createElement('div');
    el.className = 'flash' + (level === 'success' || level === 'warning' ? ' flash-' + level : '');
    el.textContent = message;
    getFlashContainer().appendChild(el);
    attachFlashDismiss(el);
}

function appendFreshFlashes(freshDoc) {
    const freshFlashes = freshDoc.querySelectorAll('.flashes .flash');
    if (!freshFlashes.length) return;
    const liveContainer = getFlashContainer();
    freshFlashes.forEach((el) => {
        const clone = el.cloneNode(true);
        liveContainer.appendChild(clone);
        attachFlashDismiss(clone);
    });
}

// ---------------------------------------------------------------
// Soft navigation: sidebar and same-app links/forms fetch the target page and swap
// only .content (like softRefresh()), so the sidebar keeps its scroll/hover state.
// Forms are fetched too (fetch() follows the redirect), then .content is swapped and
// the redirect's flash message pulled in (appendFreshFlashes), so scroll is kept.
// Not intercepted (real navigations):
//   - cross-origin, hash, mailto:/tel: or download links
//   - target="_blank" links/forms (file downloads and receipts, not HTML)
//   - GET forms
//   - data-no-soft-nav / data-no-soft-submit (e.g. the sign-out form)
// Last resort: a response without a real .content (checked via swapContent()'s return
// value) falls back to a real navigation, covering expired sessions and file endpoints.
function initSoftNav() {
    let navToken = 0;

    function isInternalNavigableLink(link) {
        if (!link || !link.href) return false;
        if (link.target && link.target !== '_self') return false;
        if (link.hasAttribute('download')) return false;
        if (link.dataset.noSoftNav !== undefined) return false;
        if (link.getAttribute('href').charAt(0) === '#') return false;
        let url;
        try {
            url = new URL(link.href, window.location.href);
        } catch (e) {
            return false;
        }
        if (url.origin !== window.location.origin) return false;
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
        return true;
    }

    function isSoftSubmittableForm(form, submitter) {
        if (form.dataset.noSoftSubmit !== undefined) return false;
        if ((form.method || 'get').toLowerCase() !== 'post') return false;
        const target = (submitter && submitter.formTarget) || form.target;
        if (target && target !== '_self') return false;
        let url;
        try {
            url = new URL(form.action, window.location.href);
        } catch (e) {
            return false;
        }
        if (url.origin !== window.location.origin) return false;
        return true;
    }

    // Every navigation funnels through here. `request` performs the fetch and returns
    // Promise<Response> (GET for a link, POST for a form).
    function go(request, url, historyMode, preserveScroll, isPost) {
        const myToken = ++navToken;
        const scrollY = window.scrollY;
        navProgress.start();

        request()
            .then((r) => {
                const contentType = r.headers.get('Content-Type') || '';
                if (contentType.indexOf('text/html') === -1) {
                    return Promise.reject(new Error('not html'));
                }
                return r.text().then((html) => ({ html, finalUrl: r.url }));
            })
            .then(({ html, finalUrl }) => {
                // A newer soft-nav started while this was in flight: drop this stale response.
                if (myToken !== navToken) return;
                // No-op if no form showed it (see showLoadingOverlay()).
                hideLoadingOverlay();
                navProgress.done();

                const fresh = new DOMParser().parseFromString(html, 'text/html');
                if (!swapContent(fresh, preserveScroll)) {
                    // finalUrl is where the request ended up (e.g. /login after session expiry); go
                    // there directly to avoid repeating the redirect. A POST response that can't be
                    // swapped must not be re-requested as a GET (405); reload the current page.
                    if (isPost) window.location.reload(); else window.location.href = finalUrl;
                    return;
                }
                appendFreshFlashes(fresh);

                if (historyMode === 'push') {
                    history.pushState({ softNav: true }, '', finalUrl);
                } else if (historyMode === 'replace') {
                    history.replaceState({ softNav: true }, '', finalUrl);
                }
                if (historyMode === 'pop' || preserveScroll) {
                    // 'pop' (back/forward) restores the old scroll. preserveScroll is for a same-path
                    // soft nav (e.g. a tab/filter link): snapping to top would feel like a reload.
                    window.scrollTo({ top: scrollY, behavior: 'auto' });
                } else {
                    window.scrollTo({ top: 0, behavior: 'auto' });
                }
            })
            .catch(() => {
                if (myToken !== navToken) return;
                hideLoadingOverlay();
                navProgress.done();
                if (isPost) window.location.reload(); else window.location.href = url;
            });
    }

    document.addEventListener('click', (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const link = e.target.closest('a[href]');
        if (!link) return;
        // The phone dock (.m-tabbar, mobile.css) is outside both but navigates like the sidebar.
        if (!link.closest('.sidebar') && !link.closest('.content') && !link.closest('.m-tabbar')) return;
        if (!isInternalNavigableLink(link)) return;

        const url = new URL(link.href, window.location.href);
        if (isSamePageNoOpLink(link)) {
            // e.g. clicking the already-open sidebar tab: must be a true no-op. The default
            // would reload the page, resetting scroll and collapsing the CSS :hover/:focus-within
            // sidebar as if the click had closed it.
            e.preventDefault();
            return;
        }
        if (url.pathname === window.location.pathname && url.search === window.location.search) {
            // Same page with a real #hash: let the browser do the anchor jump.
            return;
        }

        e.preventDefault();
        // Same path, different query (a tab/filter link): keep scroll; see go()'s preserveScroll.
        const preserveScroll = url.pathname === window.location.pathname;
        go(() => fetch(link.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }), link.href, 'push', preserveScroll);
    });

    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (!form.closest('.content')) return;
        const submitter = e.submitter;
        if (!isSoftSubmittableForm(form, submitter)) return;

        e.preventDefault();
        const formData = new FormData(form, submitter);
        // Opt-in for a form that only edits one line of an in-page list (cart row, status
        // dropdown): no scroll-to-top repaint, same as the tab/filter links above.
        const preserveScroll = form.hasAttribute('data-preserve-scroll');
        go(
            () => fetch(form.action, {
                method: 'POST',
                body: formData,
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            }),
            form.action,
            'push',
            preserveScroll,
            true,
        );
    });

    window.addEventListener('popstate', () => {
        go(() => fetch(window.location.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }), window.location.href, 'pop');
    });
}

// ---------------------------------------------------------------
// Custom select: enhances every <select> with a styled trigger + dropdown panel, since
// native pickers on phones are OS-drawn and can't be styled or sized.
// The real <select> stays in the DOM (name/id/required, form position), laid invisibly
// over the trigger with pointer-events off, so validation and the submitted value are
// untouched. A select can opt out with `data-cs-skip` (currently unused).
// Other code sets `.value` or rebuilds <option>s directly, so two hooks catch it all:
//   - a `.value` property override that re-syncs the trigger label
//   - a MutationObserver on the <option> list and the disabled attribute
// Re-run after softRefresh() swaps in fresh markup; dataset.csEnhanced makes it idempotent.
// Stock-movement types in lifecycle order, so the Ledger legend order is stable.
var MOVEMENT_TYPE_ORDER = ['PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE',
    'PACKAGE_ORDER', 'PACKAGE_RETURN', 'SALE_VOID'];


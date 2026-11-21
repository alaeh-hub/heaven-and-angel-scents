// Phone bottom dock (markup in base.html, styles in css/mobile.css, phones only).
//   - a pill springs to the current tab (Motion); taps pop the icon (anime.js)
//   - tabs rise in on first load; scrolling down tucks the dock away
// Motion is skipped under prefers-reduced-motion; CSS alone still draws the active tab.
(function () {
    const bar = document.getElementById('mTabbar');
    const pill = document.getElementById('mTabPill');
    if (!bar || !pill) return;

    const root = document.documentElement;
    const phone = window.matchMedia('(max-width: 760px)');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const M = window.Motion;
    const A = window.anime;
    const SPRING = { type: 'spring', stiffness: 420, damping: 32 };
    let placedAt = -1;

    // Spring that writes the style each frame; Motion's element animation snaps back
    // for a frame before committing, a plain number does not.
    function spring(state, to, apply, options) {
        if (state.control) state.control.stop();
        if (!M || reduce.matches) {
            state.value = to;
            apply(to);
            return;
        }
        state.control = M.animate(state.value, to, Object.assign({ type: 'spring' }, options, {
            onUpdate: (v) => { state.value = v; apply(v); },
            onComplete: () => { state.value = to; apply(to); }
        }));
    }
    const pillState = { value: 0, control: null };
    const dockState = { value: 0, control: null };

    function currentTab() {
        return bar.querySelector('.m-tab.active:not(.m-tab-primary)');
    }

    // Move the pill behind a tab (fades out for Sell, which is a disc).
    function placeAt(tab, animate) {
        if (!phone.matches) { root.classList.remove('m-pill-on'); return; }
        if (!tab) { pill.style.opacity = '0'; placedAt = -1; return; }
        const width = tab.offsetWidth - 8;
        const x = tab.offsetLeft + 4;
        const first = !root.classList.contains('m-pill-on');
        pill.style.width = width + 'px';
        pill.style.opacity = '';
        // Already there: do not restart the spring.
        if (!first && x === placedAt) return;
        placedAt = x;
        const apply = (v) => { pill.style.transform = 'translateX(' + v + 'px)'; };
        if (first || !animate) {
            if (pillState.control) pillState.control.stop();
            pillState.value = x;
            apply(x);
        } else {
            spring(pillState, x, apply, { stiffness: 420, damping: 32 });
        }
        root.classList.add('m-pill-on');
    }

    function sync(animate) { placeAt(currentTab(), animate); }

    // Tap feedback: squash, overshoot, settle. Sell also tilts like a cart tipping in.
    function pop(tab) {
        if (!A || reduce.matches) return;
        const icon = tab.querySelector('.m-tab-ico');
        if (!icon) return;
        A.animate(icon, {
            scale: [
                { to: 0.78, duration: 80, ease: 'outQuad' },
                { to: 1.14, duration: 150, ease: 'outQuad' },
                { to: 1, duration: 150, ease: 'inOutQuad' }
            ],
            rotate: tab.classList.contains('m-tab-primary')
                ? [{ to: -12, duration: 120 }, { to: 6, duration: 140 }, { to: 0, duration: 140 }]
                : 0
        });
    }

    bar.addEventListener('pointerdown', (event) => {
        const tab = event.target.closest('.m-tab');
        if (tab) pop(tab);
    });

    // The pill moves the moment you tap, before the next page has loaded.
    bar.addEventListener('click', (event) => {
        const tab = event.target.closest('a.m-tab');
        if (!tab) return;
        placeAt(tab.classList.contains('m-tab-primary') ? null : tab, true);
    });

    // The page said which tab is current (a soft navigation swapped the classes).
    new MutationObserver(() => sync(true)).observe(bar, {
        attributes: true, attributeFilter: ['class'], subtree: true
    });

    // Flag when a dialog owns the screen (dock and Halo step aside); a class on <html>
    // avoids a costly body:has() re-check on every inline-style change.
    const SHEETS = '.rs-dialog, .pg-overlay, .void-overlay, .pi-modal-overlay, .temp-overlay, .confirm-overlay, .settings-overlay';

    function sheetOpen(el) {
        if (el.matches('.rs-dialog, .pg-overlay, .void-overlay')) return el.style.display === 'flex';
        if (el.matches('.pi-modal-overlay')) return el.classList.contains('open');
        if (el.matches('.confirm-overlay, .settings-overlay')) return !el.hidden;
        return true; // .temp-overlay exists only while it is showing
    }

    function syncSheets() {
        const open = Array.prototype.some.call(document.querySelectorAll(SHEETS), sheetOpen);
        root.classList.toggle('m-sheet-open', open);
    }

    function touchesSheet(record) {
        if (record.type === 'attributes') return record.target.matches && record.target.matches(SHEETS);
        const nodes = Array.prototype.slice.call(record.addedNodes).concat(Array.prototype.slice.call(record.removedNodes));
        return nodes.some((n) => n.nodeType === 1 && (n.matches(SHEETS) || n.querySelector(SHEETS)));
    }

    new MutationObserver((records) => {
        if (records.some(touchesSheet)) syncSheets();
    }).observe(document.body, {
        subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'hidden', 'class']
    });

    // Page title in the top bar settles in on soft navigation (opacity/transform only).
    const title = document.querySelector('.topbar-title h1');
    if (title) {
        new MutationObserver(() => {
            if (!M || reduce.matches || !phone.matches) return;
            M.animate(
                title,
                { opacity: [0, 1], transform: ['translateY(5px)', 'translateY(0px)'] },
                { duration: 0.28, easing: [0.16, 1, 0.3, 1] }
            );
        }).observe(title, { childList: true, characterData: true, subtree: true });
    }

    // First load: the tabs rise in, the Sell disc pops last.
    function enter() {
        if (!A || reduce.matches || !phone.matches) return;
        try {
            if (sessionStorage.getItem('mDockIn')) return;
            sessionStorage.setItem('mDockIn', '1');
        } catch (e) { }
        const tabs = Array.from(bar.querySelectorAll('.m-tab'));
        A.animate(tabs, {
            translateY: [16, 0],
            opacity: [0, 1],
            delay: A.stagger(55),
            duration: 480,
            ease: 'outCubic'
        });
        const disc = bar.querySelector('.m-tab-primary .m-tab-ico');
        if (disc) {
            A.animate(disc, { scale: [0.4, 1], duration: 560, delay: 260, ease: 'outBack' });
        }
    }

    // Scroll down hides the dock; scroll up, reaching an end, or tabbing into it shows it.
    let away = false;
    let lastY = window.scrollY;
    let queued = false;

    function setAway(next) {
        if (next === away) return;
        away = next;
        root.classList.toggle('m-dock-away', next);
        const bottom = parseFloat(getComputedStyle(bar).bottom) || 0;
        const distance = bar.offsetHeight + bottom + 24;
        const apply = (v) => { bar.style.transform = v === 0 ? '' : 'translateY(' + v + 'px)'; };
        spring(dockState, next ? distance : 0, apply, { stiffness: 380, damping: 34 });
    }

    function onScroll() {
        queued = false;
        if (!phone.matches) { setAway(false); return; }
        const y = window.scrollY;
        const delta = y - lastY;
        const atEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 4;
        const drawerOpen = document.body.style.overflow === 'hidden';
        if (y < 80 || atEnd || drawerOpen) {
            setAway(false);
        } else if (Math.abs(delta) > 6) {
            setAway(delta > 0);
        }
        lastY = y;
    }

    window.addEventListener('scroll', () => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(onScroll);
    }, { passive: true });

    bar.addEventListener('focusin', () => setAway(false));
    window.addEventListener('resize', () => { sync(false); onScroll(); });
    window.addEventListener('orientationchange', () => sync(false));
    window.addEventListener('load', () => sync(false));
    if (phone.addEventListener) {
        phone.addEventListener('change', () => { sync(false); onScroll(); });
    }

    sync(false);
    syncSheets();
    enter();
})();

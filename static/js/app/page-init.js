// Theme toggle, fill bars, count-up, entrance motion, login splash, DOMContentLoaded boot
// (Part of the app's main script; loaded in order by base.html.)

function initThemeToggle() {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;

    const root = document.documentElement;

    btn.addEventListener('click', () => {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        try {
            localStorage.setItem('theme', next);
        } catch (e) {

        }

        // The new theme spreads in a circle from the button (one clipped snapshot is much
        // cheaper than transitioning every element's colours).
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            const r = btn.getBoundingClientRect();
            const x = r.left + r.width / 2;
            const y = r.top + r.height / 2;
            const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
            root.classList.add('vt-theme');
            const transition = document.startViewTransition(() => {
                root.setAttribute('data-theme', next);
            });
            transition.ready.then(() => {
                root.animate(
                    { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
                    { duration: 560, easing: 'cubic-bezier(0.65, 0, 0.35, 1)', pseudoElement: '::view-transition-new(root)' }
                );
            }).catch(() => { });
            transition.finished.finally(() => root.classList.remove('vt-theme'));
        } else {
            root.classList.add('theme-transitioning');
            root.setAttribute('data-theme', next);
            const isTouch = window.matchMedia('(hover: none), (pointer: coarse)').matches;
            window.setTimeout(() => root.classList.remove('theme-transitioning'), isTouch ? 180 : 400);
        }

        if (window.Motion && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            var toggleAnim = window.Motion.animate(
                btn,
                { transform: ['scale(1)', 'scale(1.15)', 'scale(1)'] },
                { duration: 0.4, easing: [0.34, 1.56, 0.64, 1] }
            );

            if (toggleAnim && typeof toggleAnim.then === 'function') {
                toggleAnim.then(null, function () { });
            }
        }
    });
}

// Sets each fill-bar to its data-pct width; starts at 0% and waits a frame so the
// .fill-bar CSS transition has a "from" state to animate.
function initFillBars() {
    document.querySelectorAll('.fill-bar[data-pct]').forEach((el) => {
        const pct = parseFloat(el.dataset.pct) || 0;
        el.style.setProperty('--pct', '0');
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                el.style.setProperty('--pct', String(Math.min(Math.max(pct, 0), 100) / 100));
            });
        });
    });
}

// Counts a stat tile up from 0 to its value. Only plain integers (counts); currency,
// comma or decimal values are left alone. Once per element (data-countup-done), since
// revealContent() re-fires on every soft nav.
function initStatCountUp() {
    if (!window.anime || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    document.querySelectorAll('.stat-value').forEach((el) => {
        if (el.dataset.countupDone) return;
        const raw = el.textContent.trim();
        if (!/^\d+$/.test(raw)) return;
        const target = parseInt(raw, 10);
        if (!target) return;

        el.dataset.countupDone = '1';
        const counter = { val: 0 };
        window.anime.animate(counter, {
            val: target,
            duration: 700,
            ease: 'outExpo',
            onUpdate: () => { el.textContent = String(Math.round(counter.val)); },
            onComplete: () => { el.textContent = String(target); },
        });
    });
}

// Stat tiles spring up and table rows slide in so a new screen builds top to bottom.
// Inline transform/opacity are cleared after so CSS hover lift still works. Capped to
// the first 14 rows; skipped under reduced motion and on tab-style soft navs (skipReveal).
function initEntranceStagger() {
    // Intentionally empty: one reveal per page now (see revealContent).
}

function revealContent() {
    // Soft navs already cross-fade via the view transition (style.css); no second animation.
    // Browsers without it get a plain opacity fade.
    if (typeof document.startViewTransition === 'function') return;
    if (!window.Motion || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const content = document.querySelector('.content');
    if (!content) return;

    var revealAnim = window.Motion.animate(content, { opacity: [0, 1] }, { duration: 0.18, easing: 'ease-out' });
    if (revealAnim && typeof revealAnim.then === 'function') {
        revealAnim.then(null, function () { });
    }
}

// Holds #loginSplash (shown once after sign-in) briefly, fades it out via
// .login-splash-exit, then removes it from the DOM. Plain CSS + setTimeout.
// With motion allowed the logo video plays first and the splash exits when it ends.
// Click or any key skips it; a hard cap covers a stalled video; if autoplay is
// refused it falls back to the static wordmark.
const LOGIN_SPLASH_HOLD_MS = 2400;
const LOGIN_SPLASH_REDUCED_HOLD_MS = 600;
const LOGIN_SPLASH_EXIT_MS = 350;

function initLoginSplash() {
    const el = document.getElementById('loginSplash');
    if (!el) return;

    let done = false;
    const dismiss = () => {
        if (done) return;
        done = true;
        document.removeEventListener('keydown', dismiss);
        el.classList.add('login-splash-exit');
        setTimeout(() => el.remove(), LOGIN_SPLASH_EXIT_MS);
    };

    // Click or any key skips it; otherwise it leaves once the reveal has played.
    el.addEventListener('click', dismiss);
    document.addEventListener('keydown', dismiss);
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(dismiss, reduceMotion ? LOGIN_SPLASH_REDUCED_HOLD_MS : LOGIN_SPLASH_HOLD_MS);
}

// Mascot (js/mascot.js): its [data-mascot] slots live in the shell, outside .content,
// so this only needs to run on a real page load (login mounts its own).
function initMascots() {
    if (!window.Mascot) return;
    window.Mascot.initAll();
}

// Runs `fn`, logging and swallowing errors, so one broken widget can't skip the
// remaining inits (initSoftNav() included, which would fall back to full reloads).
function safeInit(fn, label) {
    try {
        return fn();
    } catch (err) {
        console.error('[init:' + label + ']', err);
        return undefined;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    safeInit(initLoginSplash, 'loginSplash');
    safeInit(initNavAccordion, 'navAccordion');
    safeInit(initMobileSidebar, 'mobileSidebar');
    safeInit(initSidebarNavTooltips, 'sidebarNavTooltips');
    safeInit(initSidebarPinToggle, 'sidebarPinToggle');
    safeInit(initSidebarHoverIntent, 'sidebarHoverIntent');
    safeInit(initSidebarIndicator, 'sidebarIndicator');
    safeInit(initSidebarSearch, 'sidebarSearch');
    safeInit(initTopbarSearch, 'topbarSearch');
    safeInit(registerServiceWorker, 'serviceWorker');
    safeInit(initSidebarKeyboardNav, 'sidebarKeyboardNav');
    safeInit(initSidebarScrollShadow, 'sidebarScrollShadow');
    safeInit(initThemeToggle, 'themeToggle');
    safeInit(initSettings, 'settings');
    safeInit(initPhClock, 'phClock');
    safeInit(initTopbarMotion, 'topbarMotion');
    safeInit(initTodaySalesChip, 'todaySalesChip');
    safeInit(revealContent, 'revealContent');
    safeInit(initStatCountUp, 'statCountUp');
    safeInit(initEntranceStagger, 'entranceStagger');
    safeInit(initBelowFoldReveal, 'belowFoldReveal');
    safeInit(initFieldShake, 'fieldShake');
    safeInit(initFillBars, 'fillBars');
    safeInit(initMascots, 'mascots');

    safeInit(() => document.querySelectorAll('.flash').forEach(attachFlashDismiss), 'flashDismiss');

    safeInit(initConfirmDialogs, 'confirmDialogs');
    safeInit(initConfirmModal, 'confirmModal');

    safeInit(initSmartTables, 'smartTables');
    safeInit(initSmartLists, 'smartLists');
    safeInit(initDispatchQtyWarnings, 'dispatchQtyWarnings');
    safeInit(initCustomSelects, 'customSelects');
    safeInit(initDatePickers, 'datePickers');
    safeInit(watchForNewFormControls, 'watchFormControls');
    // Register BEFORE initSoftNav(): both are bubble-phase delegated listeners and fire in
    // registration order. initSoftNav() preventDefault()s nearly every form, which
    // e.defaultPrevented can't tell from a real cancel (confirm dialog, validation).
    safeInit(initSubmitLoadingState, 'submitLoadingState');
    safeInit(initGenerateFileFeedback, 'generateFileFeedback');
    safeInit(initSoftNav, 'softNav');
    const notifBell = safeInit(initNotificationBell, 'notificationBell');
    safeInit(() => initRealtime(notifBell), 'realtime');
    safeInit(initHaloWidget, 'haloWidget');
});

// Phones: the floating assistant button tucks away on scroll down, returns on scroll up/top/end.
(function initHaloTuck() {
    const widget = document.querySelector('.halo-widget');
    if (!widget) return;
    const phone = window.matchMedia('(max-width: 680px)');
    let lastY = window.scrollY;
    let ticking = false;
    function update() {
        ticking = false;
        const y = window.scrollY;
        const dy = y - lastY;
        const nearEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
        if (!phone.matches || y < 80 || nearEnd || dy < -6) {
            widget.classList.remove('is-tucked');
        } else if (dy > 6) {
            widget.classList.add('is-tucked');
        }
        lastY = y;
    }
    window.addEventListener('scroll', () => {
        if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });
})();

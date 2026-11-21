// Delegated actions, confirm dialogs/modals, overlays, submit loading state
// (Part of the app's main script; loaded in order by base.html.)

// Delegated actions (CSP forbids inline on*=), bound on `document` so soft-nav swaps survive:
//   data-call="fnName"            click -> window.fnName(element)
//   data-autosubmit (on <select>)  change -> submit its form
//   data-reveal-target="id" data-reveal-value="v"
//                                  change -> show #id only when value === v
function initDelegatedActions() {
    document.addEventListener('click', (e) => {
        const el = e.target.closest ? e.target.closest('[data-call]') : null;
        if (!el) return;
        const fn = window[el.dataset.call];
        if (typeof fn === 'function') fn(el);
    });
    document.addEventListener('change', (e) => {
        const el = e.target;
        if (!(el instanceof HTMLElement)) return;
        if (el.matches('select[data-autosubmit]') && el.form) el.form.submit();
        if (el.dataset.revealTarget) {
            const target = document.getElementById(el.dataset.revealTarget);
            if (target) target.style.display = el.value === el.dataset.revealValue ? 'block' : 'none';
        }
    });
}

// Confirm pattern: <form data-confirm="Message"> is handled by one delegated
// capture-phase listener. A toggle form (.../toggle) without data-confirm gets a
// default from its button label ("Deactivate?" / "Reactivate?").
// Delegated on `document`, so it survives softRefresh() content swaps.
function initConfirmDialogs() {
    initDelegatedActions();
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        // Second pass, after the person confirmed in the dialog below.
        if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }

        let message = form.dataset.confirm;
        if (!message && form.matches('form[action*="/toggle"]')) {
            const btn = form.querySelector('button[type="submit"]');
            const label = btn ? btn.textContent.trim() : 'do this';
            message = `${label}?`;
        }

        if (!message) return;
        // Styled appConfirm dialog; the browser confirm() is only a fallback.
        if (!window.appConfirm) {
            if (!confirm(message)) e.preventDefault();
            return;
        }
        // Hold the submit, ask, then requestSubmit() so validation, loading overlay and soft-nav run.
        e.preventDefault();
        e.stopImmediatePropagation();
        const submitter = e.submitter || form.querySelector('button[type="submit"]');
        const label = submitter ? submitter.textContent.trim() : '';
        const q = message.indexOf('? ');
        window.appConfirm({
            title: q > 0 ? message.slice(0, q + 1) : 'Are you sure?',
            detail: q > 0 ? message.slice(q + 2) : message,
            ok: label && label.length <= 24 ? label : 'Confirm',
            tone: /^(reactivate|activate|enable|approve)/i.test(label) ? 'accent' : 'danger',
        }).then((ok) => {
            if (!ok) return;
            form.dataset.confirmed = '1';
            if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
            else form.submit();
        });
    }, true);
}

// Styled confirm variant (e.g. Sign out): data-confirm-dialog="Title",
// optional data-confirm-detail and data-confirm-ok. Built lazily on <body>.
function initConfirmModal() {
    let overlay = null;
    let pendingForm = null;
    let pendingResolve = null;
    let returnFocus = null;

    function build() {
        overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="confirm-dialog" role="alertdialog" aria-modal="true" ' +
            'aria-labelledby="confirmDialogTitle" aria-describedby="confirmDialogDetail">' +
            '<h2 class="confirm-dialog-title" id="confirmDialogTitle"></h2>' +
            '<p class="confirm-dialog-detail" id="confirmDialogDetail"></p>' +
            '<div class="confirm-dialog-actions">' +
            '<button type="button" class="btn btn-ghost" data-confirm-cancel>Cancel</button>' +
            '<button type="button" class="btn btn-danger" data-confirm-accept></button>' +
            '</div></div>';
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.closest('[data-confirm-cancel]')) close();
            else if (e.target.closest('[data-confirm-accept]')) accept();
        });
    }

    function close() {
        if (!overlay || overlay.hidden) return;
        overlay.hidden = true;
        pendingForm = null;
        if (pendingResolve) { const r = pendingResolve; pendingResolve = null; r(false); }
        if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    function accept() {
        if (pendingResolve) {
            const r = pendingResolve;
            pendingResolve = null;
            overlay.hidden = true;
            pendingForm = null;
            r(true);
            return;
        }
        const form = pendingForm;
        overlay.hidden = true;
        pendingForm = null;
        // submit() skips the submit event, so this doesn't re-open the dialog.
        if (form) form.submit();
    }

    document.addEventListener('keydown', (e) => {
        if (!overlay || overlay.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const btns = overlay.querySelectorAll('button');
            const first = btns[0];
            const last = btns[btns.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.dataset.confirmDialog) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!overlay) build();
        pendingForm = form;
        returnFocus = document.activeElement;
        overlay.querySelector('.confirm-dialog-title').textContent = form.dataset.confirmDialog;
        const detail = overlay.querySelector('.confirm-dialog-detail');
        detail.textContent = form.dataset.confirmDetail || '';
        detail.hidden = !detail.textContent;
        const okBtn = overlay.querySelector('[data-confirm-accept]');
        okBtn.textContent = form.dataset.confirmOk || 'Confirm';
        okBtn.className = 'btn btn-danger';
        overlay.hidden = false;
        overlay.querySelector('[data-confirm-cancel]').focus();
    }, true);

    // window.appConfirm({ title, detail, ok, tone }) -> Promise<boolean>: styled confirm();
    // true on the action button, false on Cancel, Escape or outside click.
    window.appConfirm = function (o) {
        if (!overlay) build();
        if (pendingResolve) { const r = pendingResolve; pendingResolve = null; r(false); }
        pendingForm = null;
        returnFocus = document.activeElement;
        overlay.querySelector('.confirm-dialog-title').textContent = o.title || 'Are you sure?';
        const detail = overlay.querySelector('.confirm-dialog-detail');
        detail.textContent = o.detail || '';
        detail.hidden = !detail.textContent;
        const okBtn = overlay.querySelector('[data-confirm-accept]');
        okBtn.textContent = o.ok || 'Confirm';
        okBtn.className = 'btn ' + (o.tone === 'accent' ? 'btn-accent' : 'btn-danger');
        overlay.hidden = false;
        overlay.querySelector('[data-confirm-cancel]').focus();
        return new Promise(function (resolve) { pendingResolve = resolve; });
    };
}

// Full-screen "Loading" dialog shown during a form submission (.loading-overlay).
// Built lazily on <body>, not .content, so it works on standalone pages (login) and
// survives soft-nav content swaps.
let loadingOverlayEl = null;

function getLoadingOverlay() {
    if (loadingOverlayEl) return loadingOverlayEl;
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<div class="loading-dialog">' +
        '<div class="loading-dialog-spinner"></div>' +
        '<div class="loading-dialog-text">Loading…</div>' +
        '</div>';
    document.body.appendChild(el);
    loadingOverlayEl = el;
    return el;
}

// Minimum visible time, so a fast request doesn't flash the dialog; also longer than
// the 0.25s CSS entrance animation (.loading-dialog-in).
const LOADING_OVERLAY_MIN_VISIBLE_MS = 400;
// Must match .loading-overlay-exit's animation-duration in style.css.
const LOADING_OVERLAY_EXIT_MS = 180;
let loadingOverlayShownAt = 0;
// Bumped on every show; a stale delayed hide (e.g. after back/forward) is ignored
// so it can't close a dialog a newer request just reopened.
let loadingOverlayToken = 0;

// Plain CSS animations, not Motion: a page navigation can end this dialog at any
// moment, and Motion's animate() then rejects with an unhandled AbortError.
function showLoadingOverlay() {
    loadingOverlayShownAt = Date.now();
    loadingOverlayToken += 1;
    const el = getLoadingOverlay();
    el.classList.remove('loading-overlay-exit');
    el.hidden = false;
}

function hideLoadingOverlay() {
    if (!loadingOverlayEl || loadingOverlayEl.hidden) return;
    const myToken = loadingOverlayToken;

    const doHide = () => {
        // Superseded by a newer show(); that call owns the overlay now.
        if (myToken !== loadingOverlayToken) return;
        loadingOverlayEl.classList.add('loading-overlay-exit');
        setTimeout(() => {
            if (myToken === loadingOverlayToken) {
                loadingOverlayEl.hidden = true;
                loadingOverlayEl.classList.remove('loading-overlay-exit');
            }
        }, LOADING_OVERLAY_EXIT_MS);
    };

    // MIN_VISIBLE_MS > ENTER_MS, so the hide never beats the CSS entrance animation.
    const remaining = LOADING_OVERLAY_MIN_VISIBLE_MS - (Date.now() - loadingOverlayShownAt);
    if (remaining > 0) {
        setTimeout(doHide, remaining);
    } else {
        doHide();
    }
}

// ---------------------------------------------------------------
// Modal overlays: a fixed backdrop (inline style.display) wrapping one .card panel,
// closed by Cancel, backdrop click or Escape. openOverlay()/closeOverlay() are the
// one place that shows/hides them; wireOverlay() covers the simple one-button case.
// Uses anime.js; the close delay is a plain setTimeout matching the exit duration.
// Exposed on window so pages (e.g. users.html) can time follow-up work against it.
window.OVERLAY_EXIT_MS = 140;
const OVERLAY_EXIT_MS = window.OVERLAY_EXIT_MS;

function openOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay) return;
    overlay.style.display = 'flex';
    if (!window.anime || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const panel = overlay.querySelector('.card, .pi-modal');
    // anime.js animate() takes ONE params object (keyframes + duration/ease merged);
    // a separate third argument is silently ignored.
    window.anime.animate(overlay, { opacity: [0, 1], duration: 160, ease: 'outQuad' });
    if (panel) {
        // Rises in with a soft ease-out (same as the public site's package dialog).
        window.anime.animate(panel, { opacity: [0, 1], translateY: [24, 0], duration: 360, ease: 'outCubic' });
    }
}

function closeOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay || overlay.style.display === 'none') return;
    if (window.anime && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const panel = overlay.querySelector('.card, .pi-modal');
        window.anime.animate(overlay, { opacity: [1, 0], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        if (panel) {
            window.anime.animate(panel, { opacity: [1, 0], translateY: [0, 10], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        }
        setTimeout(() => { overlay.style.display = 'none'; }, OVERLAY_EXIT_MS);
    } else {
        overlay.style.display = 'none';
    }
}

// Common case: one open button and one Cancel button; the backdrop and Escape also close.
function wireOverlay(overlayId, openBtnId, cancelBtnId) {
    const overlay = document.getElementById(overlayId);
    const openBtn = openBtnId ? document.getElementById(openBtnId) : null;
    const cancelBtn = cancelBtnId ? document.getElementById(cancelBtnId) : null;
    if (!overlay || !openBtn) return;

    openBtn.addEventListener('click', () => openOverlay(overlay));
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeOverlay(overlay));
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeOverlay(overlay);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlay.style.display !== 'none') closeOverlay(overlay);
    });
}

// Shows the loading overlay as soon as a form is really submitted (no per-form opt-in).
// Bubble phase on `document`, so e.defaultPrevented already reflects cancels from the
// confirm dialog or a form's own validation.
// Must be registered BEFORE initSoftNav(): its submit listener also calls
// preventDefault(), which defaultPrevented can't tell from a cancel.
// Hiding is initSoftNav()'s job (go()); a plain submit unloads the page itself.
// Forms targeting another tab (e.g. report Download buttons) never navigate this tab,
// so they are skipped, using the same target check as isSoftSubmittableForm().
function initSubmitLoadingState() {
    // A submit marks its button busy and starts the top progress bar; a second submit
    // of the same form is swallowed while the first is in flight.
    function clearBusy(form) {
        delete form.dataset.submitting;
        form.querySelectorAll('.is-loading').forEach((b) => {
            b.classList.remove('is-loading');
            b.removeAttribute('aria-busy');
            b.removeAttribute('aria-disabled');
        });
    }

    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        if (!(e.target instanceof HTMLFormElement)) return;
        const form = e.target;
        const target = (e.submitter && e.submitter.formTarget) || form.target;
        if (target && target !== '_self') return;
        if (form.dataset.submitting === '1') {
            e.preventDefault();
            return;
        }
        form.dataset.submitting = '1';
        const btn = e.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (btn) {
            btn.classList.add('is-loading');
            btn.setAttribute('aria-busy', 'true');
            btn.setAttribute('aria-disabled', 'true');
        }
        const bar = document.getElementById('topbarProgress');
        if (bar) bar.classList.add('is-loading');
        // A later handler may still cancel the submit (validation): undo then.
        setTimeout(() => {
            if (e.defaultPrevented) {
                clearBusy(form);
                if (bar) bar.classList.remove('is-loading');
            }
        }, 0);
    });

    // Back/forward restores the page from cache with the busy state still on.
    window.addEventListener('pageshow', (ev) => {
        if (!ev.persisted) return;
        document.querySelectorAll('form[data-submitting]').forEach(clearBusy);
        const bar = document.getElementById('topbarProgress');
        if (bar) bar.classList.remove('is-loading');
    });
}

// ---- File generation (receipts, report PDF/Excel, CSV export: [data-generate-file]) ----
// These build a file server-side and open in a new tab, so nothing else shows progress.
// fetch() lets the dialog show real progress and close when the file is ready.
// A non-file response (e.g. a "no data" redirect) is treated as a normal navigation.
let generateOverlayEl = null;
let generateInFlight = false;
let generateOverlayShownAt = 0;
// Minimum visible time for report dialogs, so a fast receipt doesn't just blip.
const GENERATE_OVERLAY_MIN_VISIBLE_MS = 900;


// Custom selects, datalist inputs and date pickers
// (Part of the app's main script; loaded in order by base.html.)

function initCustomSelects(root) {
    (root || document).querySelectorAll('select').forEach(enhanceSelect);
    (root || document).querySelectorAll('input[list]').forEach(enhanceDatalistInput);
}

// Replaces the browser's <datalist> popup with the custom select panel; the input keeps
// its name/id/value and fires the same input + change events.
function enhanceDatalistInput(input) {
    if (input.dataset.dlEnhanced) return;
    const dl = document.getElementById(input.getAttribute('list'));
    if (!dl) return;
    input.dataset.dlEnhanced = '1';
    input.removeAttribute('list');

    const wrap = document.createElement('div');
    wrap.className = 'cs-select';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const panel = document.createElement('div');
    panel.className = 'cs-panel';
    panel.setAttribute('role', 'listbox');
    wrap.appendChild(panel);

    let items = [];
    let active = -1;

    const close = () => {
        wrap.classList.remove('cs-open');
        active = -1;
    };
    const mark = () => {
        Array.from(panel.children).forEach((el, i) => el.classList.toggle('cs-active', i === active));
        const el = panel.children[active];
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    };
    const pick = (value) => {
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close();
    };
    const render = () => {
        const q = input.value.trim().toLowerCase();
        // <option label="..."> text is also searched and shown, so a code is found by its name.
        const hints = {};
        const all = Array.from(dl.options).filter((o) => o.value && o.value.toLowerCase() !== q).map((o) => {
            hints[o.value] = o.getAttribute('label') || '';
            return o.value;
        });
        items = (window.SmartMatch
            ? window.SmartMatch.rank(all, q, (v) => v, (v) => hints[v])
            : all.filter((v) => v.toLowerCase().includes(q))).slice(0, 8);
        panel.replaceChildren();
        items.forEach((v) => {
            const el = document.createElement('div');
            el.className = 'cs-option';
            el.setAttribute('role', 'option');
            if (window.SmartMatch && q) el.appendChild(window.SmartMatch.highlight(v, q, 'cs-match'));
            else el.textContent = v;
            if (hints[v]) {
                const hint = document.createElement('span');
                hint.className = 'cs-option-desc';
                hint.textContent = ' ' + hints[v];
                el.appendChild(hint);
            }
            el.addEventListener('mousedown', (e) => e.preventDefault());
            el.addEventListener('click', () => pick(v));
            panel.appendChild(el);
        });
        active = -1;
        if (items.length) wrap.classList.add('cs-open');
        else close();
    };

    input.addEventListener('focus', render);
    input.addEventListener('input', render);
    input.addEventListener('blur', close);
    input.addEventListener('keydown', (e) => {
        if (!wrap.classList.contains('cs-open')) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            active = (active + 1) % items.length;
            mark();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            active = (active - 1 + items.length) % items.length;
            mark();
        } else if (e.key === 'Enter' && active >= 0) {
            e.preventDefault();
            pick(items[active]);
        } else if (e.key === 'Escape') {
            close();
        }
    });
}

function enhanceSelect(select) {
    if (select.dataset.csEnhanced || select.multiple || select.dataset.csSkip !== undefined) return;
    select.dataset.csEnhanced = '1';

    const wrap = document.createElement('div');
    wrap.className = 'cs-select';
    // Optional look (e.g. "status": coloured pill with a dot and a description per option).
    if (select.dataset.csVariant) wrap.classList.add('cs-variant-' + select.dataset.csVariant);
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'cs-trigger';
    // Not a tab stop: the invisible real <select> keeps focus, Tab order and native validation.
    trigger.tabIndex = -1;
    trigger.setAttribute('aria-hidden', 'true');
    trigger.innerHTML =
        '<span class="cs-trigger-label"></span>' +
        '<svg class="cs-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="m6 9 6 6 6-6"/></svg>';
    wrap.appendChild(trigger);

    const panel = document.createElement('div');
    panel.className = 'cs-panel';
    panel.setAttribute('role', 'listbox');
    wrap.appendChild(panel);
    // The status picker's panel moves to <body> while open: a transformed ancestor would
    // re-anchor position:fixed to itself.
    const floating = wrap.classList.contains('cs-variant-status');
    if (floating) panel.classList.add('cs-panel-floating');

    const labelEl = trigger.querySelector('.cs-trigger-label');
    let triggerDot = null;
    let activeIndex = -1;

    // ---- Type-to-search: 7+ choices (or data-cs-search) get a search box; data-cs-nosearch
    // opts out. Uses the shared SmartMatch scorer; data-search="..." adds searchable text. ----
    let searchRow = null;
    let searchInput = null;
    let emptyEl = null;
    let query = '';

    function isSearchable() {
        if (floating || select.dataset.csNosearch !== undefined || !window.SmartMatch) return false;
        return select.dataset.csSearch !== undefined || select.options.length >= 7;
    }

    function ensureSearch() {
        if (searchRow) return;
        searchRow = document.createElement('div');
        searchRow.className = 'cs-search';
        searchInput = document.createElement('input');
        searchInput.type = 'text';
        searchInput.className = 'cs-search-input';
        searchInput.placeholder = 'Type to search…';
        searchInput.autocomplete = 'off';
        searchInput.spellcheck = false;
        searchInput.setAttribute('aria-label', 'Search options');
        searchRow.appendChild(searchInput);
        // Typing here must not look like an edit of the form's real field.
        ['input', 'change'].forEach(function (evt) {
            searchInput.addEventListener(evt, function (e) { e.stopPropagation(); });
        });
        searchInput.addEventListener('input', function () {
            query = searchInput.value;
            applyFilter();
            position();
        });
        searchInput.addEventListener('blur', function (e) {
            if (e.relatedTarget && wrap.contains(e.relatedTarget)) return;
            close();
        });
        // Panel clicks must not pull focus off the search box (it would blur-close the panel).
        panel.addEventListener('mousedown', function (e) {
            if (e.target !== searchInput) e.preventDefault();
        });
        emptyEl = document.createElement('div');
        emptyEl.className = 'cs-option-empty';
        emptyEl.textContent = 'No matches. Try a different spelling.';
    }

    function optionEls() {
        return Array.prototype.slice.call(panel.querySelectorAll('.cs-option'));
    }

    function visibleOptionEls() {
        return optionEls().filter(function (el) { return !el.hidden; });
    }

    function optionElFor(index) {
        return optionEls().filter(function (el) { return Number(el.dataset.index) === index; })[0] || null;
    }

    // Show only matching options, best first; an empty query restores the original order.
    function applyFilter() {
        const els = optionEls();
        const q = query.trim();
        if (emptyEl && emptyEl.parentNode) emptyEl.remove();
        els.forEach(function (el) {
            const nameEl = el.querySelector('.cs-option-name');
            if (nameEl) nameEl.textContent = select.options[Number(el.dataset.index)].textContent.trim() || ' ';
        });
        if (!q) {
            els.sort(function (a, b) { return Number(a.dataset.index) - Number(b.dataset.index); })
                .forEach(function (el) { el.hidden = false; panel.appendChild(el); });
            return;
        }
        const scored = els.map(function (el) {
            const opt = select.options[Number(el.dataset.index)];
            const s = opt.value === '' ? -1
                : window.SmartMatch.score(q, opt.textContent, (opt.dataset.search || '') + ' ' + (opt.dataset.desc || ''));
            return { el: el, opt: opt, s: s };
        });
        const hits = scored.filter(function (r) { return r.s !== -1; })
            .sort(function (a, b) { return a.s - b.s || Number(a.el.dataset.index) - Number(b.el.dataset.index); });
        scored.forEach(function (r) { r.el.hidden = r.s === -1; });
        hits.forEach(function (r) {
            panel.appendChild(r.el);
            const nameEl = r.el.querySelector('.cs-option-name');
            if (nameEl) nameEl.replaceChildren(window.SmartMatch.highlight(r.opt.textContent.trim(), q, 'cs-match'));
        });
        if (!hits.length) panel.appendChild(emptyEl);
        const first = hits.filter(function (r) { return !r.opt.disabled; })[0];
        activeIndex = first ? Number(first.el.dataset.index) : -1;
        highlightActive();
        const el = first && first.el;
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }

    function setQuery(text) {
        ensureSearch();
        query = text;
        searchInput.value = text;
        applyFilter();
        position();
    }

    function isOpen() {
        return wrap.classList.contains('cs-open');
    }

    // The status picker sits in a scrolling table, so its panel is fixed to the viewport
    // (and closed if the page scrolls).
    function positionFixed() {
        const r = trigger.getBoundingClientRect();
        const w = Math.min(272, window.innerWidth - 16);
        const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
        const below = window.innerHeight - r.bottom - 12;
        const above = r.top - 12;
        panel.style.width = w + 'px';
        panel.style.left = left + 'px';
        panel.style.right = 'auto';
        if (panel.scrollHeight > below && above > below) {
            wrap.classList.add('cs-open-up');
            panel.style.top = 'auto';
            panel.style.bottom = (window.innerHeight - r.top + 6) + 'px';
            panel.style.maxHeight = Math.min(320, above) + 'px';
        } else {
            wrap.classList.remove('cs-open-up');
            panel.style.bottom = 'auto';
            panel.style.top = (r.bottom + 6) + 'px';
            panel.style.maxHeight = Math.min(320, below) + 'px';
        }
    }

    function position() {
        if (wrap.classList.contains('cs-variant-status')) {
            positionFixed();
            return;
        }
        // Flip the panel above the trigger when there's no room below. "Room" is the smaller
        // of the viewport edge and the bottom/top of the enclosing card or modal, so a long
        // list never overhangs the next section; it scrolls internally for the rest.
        wrap.classList.remove('cs-open-up');
        panel.style.maxHeight = '';
        const rect = trigger.getBoundingClientRect();
        const host = wrap.closest('.card, .modal, [role="dialog"]');
        const hostRect = host ? host.getBoundingClientRect() : null;
        const margin = 12;

        const viewportBelow = window.innerHeight - rect.bottom;
        const spaceBelow = hostRect
            ? Math.max(0, Math.min(viewportBelow, hostRect.bottom - rect.bottom - margin))
            : viewportBelow;

        const viewportAbove = rect.top;
        const spaceAbove = hostRect
            ? Math.max(0, Math.min(viewportAbove, rect.top - Math.max(0, hostRect.top) - margin))
            : viewportAbove;

        const defaultMax = parseFloat(getComputedStyle(panel).maxHeight) || 280;
        const panelHeight = panel.scrollHeight || defaultMax;

        if (spaceBelow < panelHeight && spaceAbove > spaceBelow) {
            wrap.classList.add('cs-open-up');
            panel.style.maxHeight = Math.max(120, Math.min(defaultMax, spaceAbove)) + 'px';
        } else {
            panel.style.maxHeight = Math.max(120, Math.min(defaultMax, spaceBelow)) + 'px';
        }

        // A narrow trigger's wider panel is anchored to its right edge when it would spill.
        wrap.classList.remove('cs-align-right');
        if (panel.getBoundingClientRect().right > document.documentElement.clientWidth - 8) {
            wrap.classList.add('cs-align-right');
        }
    }

    function open() {
        if (select.disabled || isOpen()) return;
        if (isSearchable()) {
            ensureSearch();
            query = '';
            searchInput.value = '';
        }
        rebuildPanel();
        if (floating) {
            document.body.appendChild(panel);
            panel.classList.add('cs-panel-open');
        }
        wrap.classList.add('cs-open');
        trigger.setAttribute('aria-expanded', 'true');
        activeIndex = select.selectedIndex;
        highlightActive();
        position();
        const activeEl = optionElFor(activeIndex);
        if (activeEl && activeEl.scrollIntoView) activeEl.scrollIntoView({ block: 'nearest' });
        if (searchInput && isSearchable()) {
            searchInput.focus({ preventScroll: true });
            // Panel just became visible; retry once if the browser refused focus.
            if (document.activeElement !== searchInput) {
                requestAnimationFrame(function () {
                    if (isOpen()) searchInput.focus({ preventScroll: true });
                });
            }
        }
    }

    function close() {
        if (!isOpen()) return;
        // Return focus to the real <select> so Tab order continues.
        if (searchInput && document.activeElement === searchInput) select.focus({ preventScroll: true });
        wrap.classList.remove('cs-open');
        panel.classList.remove('cs-panel-open');
        // Do NOT clear 'cs-open-up' or the inline max-height here: the panel is still visible
        // during its closing transition and would jump to the top-anchored position.
        // position() resets both on every open.
        trigger.setAttribute('aria-expanded', 'false');
        activeIndex = -1;
    }

    function highlightActive() {
        optionEls().forEach(function (el) {
            el.classList.toggle('cs-active', Number(el.dataset.index) === activeIndex);
        });
    }

    // Walk the visible options (all, or search matches) in order, skipping disabled.
    function moveActive(delta) {
        const opts = visibleOptionEls();
        if (!opts.length) return;
        let pos = opts.findIndex(function (el) { return Number(el.dataset.index) === activeIndex; });
        for (let step = 0; step < opts.length; step++) {
            pos += delta;
            if (pos < 0) pos = opts.length - 1;
            if (pos > opts.length - 1) pos = 0;
            if (!select.options[Number(opts[pos].dataset.index)].disabled) break;
        }
        activeIndex = Number(opts[pos].dataset.index);
        highlightActive();
        const el = opts[pos];
        if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }

    function selectIndex(i) {
        if (i < 0 || i >= select.options.length || select.options[i].disabled) return;
        // Re-picking the current value of an auto-submitting select must not post again.
        if (i === select.selectedIndex && select.hasAttribute('data-autosubmit')) return;
        select.selectedIndex = i;
        sync();
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function rebuildPanel() {
        const opts = Array.prototype.slice.call(select.options);
        if (!opts.length) {
            panel.innerHTML = '<div class="cs-option-empty">Nothing to choose from yet.</div>';
            return;
        }
        const withSearch = isSearchable();
        if (withSearch) ensureSearch();
        // Built with textContent, not an HTML string, so user-entered option text can't inject
        // HTML. Don't switch to innerHTML without escaping.
        panel.innerHTML = '';
        if (withSearch) panel.appendChild(searchRow);
        opts.forEach(function (opt, i) {
            const el = document.createElement('div');
            el.className = 'cs-option' + (opt.disabled ? ' cs-option-disabled' : '');
            el.setAttribute('role', 'option');
            el.dataset.index = i;
            // Per-option extras: data-tone paints a leading dot, data-desc a muted second line.
            if (opt.dataset.tone) {
                const dot = document.createElement('span');
                dot.className = 'cs-dot';
                dot.dataset.tone = opt.dataset.tone;
                el.appendChild(dot);
            }
            const body = document.createElement('span');
            body.className = 'cs-option-body';
            const name = document.createElement('span');
            name.className = 'cs-option-name';
            name.textContent = opt.textContent.trim() || ' ';
            body.appendChild(name);
            if (opt.dataset.desc) {
                const desc = document.createElement('span');
                desc.className = 'cs-option-desc';
                desc.textContent = opt.dataset.desc;
                body.appendChild(desc);
            }
            el.appendChild(body);
            panel.appendChild(el);
        });
        optionEls().forEach(function (el) {
            el.addEventListener('click', function () {
                const i = parseInt(el.dataset.index, 10);
                selectIndex(i);
                close();
            });
            // preventDefault on mousedown (like the .ac-combo-menu): else focus handling can close
            // the panel before the click registers.
            el.addEventListener('mousedown', function (e) { e.preventDefault(); });
        });
        syncSelectedMarker();
        // Options were rebuilt; keep the typed search applied.
        if (withSearch && query.trim()) applyFilter();
    }

    function syncSelectedMarker() {
        optionEls().forEach(function (el) {
            el.classList.toggle('cs-selected', Number(el.dataset.index) === select.selectedIndex);
        });
    }

    function syncTrigger() {
        const opt = select.options[select.selectedIndex];
        const text = opt ? opt.textContent.trim() : '';
        const nextText = text || ' ';
        const changed = labelEl.textContent !== nextText;
        if (changed) labelEl.textContent = nextText;
        // Fade new text in when script fills a dependent dropdown (not on first fill or no-op).
        if (changed && labelEl.dataset.csReady === '1') {
            labelEl.classList.remove('cs-label-in');
            void labelEl.offsetWidth;
            labelEl.classList.add('cs-label-in');
        }
        labelEl.dataset.csReady = '1';
        labelEl.classList.toggle('cs-placeholder', !!opt && select.selectedIndex === 0 && opt.value === '');
        // Tone dot on the trigger, and the tone on the wrapper so the variant can tint the pill.
        if (opt && opt.dataset.tone) {
            if (!triggerDot) {
                triggerDot = document.createElement('span');
                triggerDot.className = 'cs-dot';
                trigger.insertBefore(triggerDot, labelEl);
            }
            triggerDot.dataset.tone = opt.dataset.tone;
            wrap.dataset.tone = opt.dataset.tone;
        }
        wrap.classList.toggle('cs-disabled', select.disabled);
    }

    function sync() {
        syncTrigger();
        syncSelectedMarker();
    }

    // ---- .value interceptor: catches `select.value = x` from any script so the label stays in sync ----
    const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    if (valueDescriptor && valueDescriptor.configurable) {
        Object.defineProperty(select, 'value', {
            configurable: true,
            enumerable: true,
            get: function () { return valueDescriptor.get.call(select); },
            set: function (v) {
                valueDescriptor.set.call(select, v);
                sync();
            },
        });
    }

    // ---- option-list rebuilds and disabled toggles from any script ----
    new MutationObserver(function () {
        rebuildPanel();
        syncTrigger();
    }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });

    // ---- native events: a real 'change' still fires on native type-ahead; keep trigger synced and close ----
    select.addEventListener('change', function () { sync(); close(); });
    select.addEventListener('focus', function () { wrap.classList.add('cs-focus'); });
    select.addEventListener('blur', function (e) {
        wrap.classList.remove('cs-focus');
        // Focus moving into the panel's own search box is not leaving.
        if (e.relatedTarget && panel.contains(e.relatedTarget)) return;
        close();
    });

    trigger.addEventListener('mousedown', function (e) { e.preventDefault(); });
    trigger.addEventListener('click', function () {
        if (select.disabled) return;
        select.focus({ preventScroll: true });
        if (isOpen()) close(); else open();
    });

    wrap.addEventListener('keydown', function (e) {
        if (select.disabled) return;
        const inSearch = !!searchInput && e.target === searchInput;
        if (e.key === 'Escape') { close(); return; }
        if (e.key === 'Tab') { close(); return; }
        if (!isOpen()) {
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                open();
            } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && isSearchable()) {
                // Typing on a focused dropdown opens it with that letter in the search box.
                e.preventDefault();
                open();
                setQuery(e.key);
            }
            return;
        }
        if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
        else if (inSearch && e.key === ' ') { /* a space is just a space while typing */ }
        else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (activeIndex >= 0) { selectIndex(activeIndex); close(); }
        }
    });

    // Page scripts rebuild rows with selects; drop these global listeners once it leaves the page.
    function onDocumentClick(e) {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen() && !wrap.contains(e.target) && !panel.contains(e.target)) close();
    }
    function onWindowResize() {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen()) position();
    }
    function onAnyScroll(e) {
        if (!wrap.isConnected) { detachGlobalListeners(); return; }
        if (isOpen() && !panel.contains(e.target)) close();
    }
    function detachGlobalListeners() {
        document.removeEventListener('click', onDocumentClick);
        window.removeEventListener('resize', onWindowResize);
        window.removeEventListener('scroll', onAnyScroll, true);
        if (floating) panel.remove();
    }
    document.addEventListener('click', onDocumentClick);
    window.addEventListener('resize', onWindowResize);
    if (wrap.classList.contains('cs-variant-status')) window.addEventListener('scroll', onAnyScroll, true);

    rebuildPanel();
    sync();
}

// Enhance selects/date inputs that scripts insert after load (a native select opens the
// OS full-screen picker on phones), so every dropdown looks the same.
function watchForNewFormControls() {
    if (typeof MutationObserver !== 'function' || !document.body) return;
    const FIELD = 'select, input[list], input[type="date"], input[type="month"]';
    const roots = new Set();
    let scheduled = false;

    new MutationObserver((mutations) => {
        mutations.forEach((m) => {
            m.addedNodes.forEach((node) => {
                if (node.nodeType !== 1 || node.tagName === 'OPTION') return;
                if (node.matches(FIELD)) roots.add(node.parentNode);
                else if (node.querySelector(FIELD)) roots.add(node);
            });
        });
        if (!roots.size || scheduled) return;
        scheduled = true;
        queueMicrotask(() => {
            scheduled = false;
            roots.forEach((root) => {
                if (!root || !root.isConnected) return;
                initCustomSelects(root);
                initDatePickers(root);
            });
            roots.clear();
        });
    }).observe(document.body, { childList: true, subtree: true });
}

// Custom date/month picker: a styled trigger + calendar panel in front of every
// <input type="date"|"month">, since the OS picker can't be styled.
// The real input stays in place (invisible, click-through; see .dp in style.css) as the
// submitted field, tab stop and the thing page scripts read/set via `.value`.
// Like enhanceSelect(): a `.value` override and a MutationObserver keep the trigger in sync.
// Opt out with `data-dp-skip`. Safe to re-run (dataset.dpEnhanced).
const DP_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];
const DP_WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function initDatePickers(root) {
    (root || document).querySelectorAll('input[type="date"], input[type="month"]').forEach(enhanceDateInput);
}

function enhanceDateInput(input) {
    if (input.dataset.dpEnhanced || input.dataset.dpSkip !== undefined) return;
    input.dataset.dpEnhanced = '1';
    const monthMode = input.type === 'month';

    const pad = (n) => String(n).padStart(2, '0');
    const toIso = (y, m, d) => monthMode ? `${y}-${pad(m + 1)}` : `${y}-${pad(m + 1)}-${pad(d)}`;
    // Parse as plain numbers: new Date("YYYY-MM-DD") is UTC and can land on the previous day.
    function parse(value) {
        const m = monthMode ? /^(\d{4})-(\d{2})$/.exec(value || '') : /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
        return m ? { y: +m[1], m: +m[2] - 1, d: monthMode ? 1 : +m[3] } : null;
    }
    const today = new Date();
    const todayIso = toIso(today.getFullYear(), today.getMonth(), today.getDate());
    const inRange = (iso) => !((input.min && iso < input.min) || (input.max && iso > input.max));

    const wrap = document.createElement('div');
    wrap.className = 'dp';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'dp-trigger';
    // Not a tab stop (like .cs-trigger); the real input stays focusable.
    trigger.tabIndex = -1;
    trigger.setAttribute('aria-hidden', 'true');
    trigger.innerHTML =
        '<span class="dp-trigger-label"></span>' +
        '<svg class="dp-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
        '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';
    wrap.appendChild(trigger);
    const labelEl = trigger.querySelector('.dp-trigger-label');

    const panel = document.createElement('div');
    panel.className = 'dp-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', monthMode ? 'Choose month' : 'Choose date');
    wrap.appendChild(panel);

    // view: month (or year) shown. cursor: keyboard-highlighted cell (ISO string).
    let view = { y: today.getFullYear(), m: today.getMonth() };
    let cursor = null;

    const isOpen = () => wrap.classList.contains('dp-open');

    function syncTrigger() {
        const p = parse(input.value);
        if (p) {
            labelEl.textContent = monthMode
                ? `${DP_MONTHS[p.m]} ${p.y}`
                : `${DP_MONTHS[p.m].slice(0, 3)} ${p.d}, ${p.y}`;
            labelEl.classList.remove('dp-placeholder');
        } else {
            labelEl.textContent = input.placeholder || (monthMode ? 'Select month' : 'Select date');
            labelEl.classList.add('dp-placeholder');
        }
        wrap.classList.toggle('dp-disabled', input.disabled);
    }

    function cell(text, iso, extraClass) {
        const b = document.createElement('button');
        b.type = 'button';
        b.tabIndex = -1;
        b.className = 'dp-cell' + (extraClass ? ' ' + extraClass : '');
        b.textContent = text;
        b.dataset.iso = iso;
        if (!inRange(iso)) b.disabled = true;
        if (iso === input.value) b.classList.add('dp-selected');
        if (iso === (monthMode ? todayIso.slice(0, 7) : todayIso)) b.classList.add('dp-today');
        if (iso === cursor) b.classList.add('dp-cursor');
        return b;
    }

    function render() {
        panel.innerHTML = '';
        const head = document.createElement('div');
        head.className = 'dp-head';
        const title = document.createElement('span');
        title.className = 'dp-title';
        title.textContent = monthMode ? String(view.y) : `${DP_MONTHS[view.m]} ${view.y}`;
        const nav = (dir, label, path) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = -1;
            b.className = 'dp-nav';
            b.setAttribute('aria-label', label);
            b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="${path}"/></svg>`;
            b.addEventListener('click', () => shiftView(dir));
            return b;
        };
        head.append(
            title,
            nav(-1, monthMode ? 'Previous year' : 'Previous month', 'm15 6-6 6 6 6'),
            nav(1, monthMode ? 'Next year' : 'Next month', 'm9 6 6 6-6 6'),
        );
        panel.appendChild(head);

        const grid = document.createElement('div');
        grid.className = monthMode ? 'dp-grid dp-grid-months' : 'dp-grid';
        if (monthMode) {
            DP_MONTHS.forEach((name, i) => grid.appendChild(cell(name.slice(0, 3), toIso(view.y, i), '')));
        } else {
            DP_WEEKDAYS.forEach((d) => {
                const w = document.createElement('span');
                w.className = 'dp-weekday';
                w.textContent = d;
                grid.appendChild(w);
            });
            // Neighbour-month days keep a steady 6-row grid so the panel height never jumps.
            const first = new Date(view.y, view.m, 1);
            const start = new Date(view.y, view.m, 1 - first.getDay());
            for (let i = 0; i < 42; i += 1) {
                const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
                const outside = d.getMonth() !== view.m;
                grid.appendChild(cell(String(d.getDate()), toIso(d.getFullYear(), d.getMonth(), d.getDate()),
                    outside ? 'dp-outside' : ''));
            }
        }
        grid.addEventListener('click', (e) => {
            const b = e.target.closest('.dp-cell');
            if (b && !b.disabled) pick(b.dataset.iso);
        });
        panel.appendChild(grid);

        const foot = document.createElement('div');
        foot.className = 'dp-foot';
        const footBtn = (text, fn, disabled) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = -1;
            b.className = 'dp-foot-btn';
            b.textContent = text;
            b.disabled = !!disabled;
            b.addEventListener('click', fn);
            return b;
        };
        const nowIso = monthMode ? todayIso.slice(0, 7) : todayIso;
        foot.append(
            footBtn('Clear', () => pick(''), input.required),
            footBtn(monthMode ? 'This month' : 'Today', () => pick(nowIso), !inRange(nowIso)),
        );
        panel.appendChild(foot);

        // Panel buttons must not steal focus from the real input (it would close the panel).
        panel.querySelectorAll('button').forEach((b) => b.addEventListener('mousedown', (e) => e.preventDefault()));
    }

    function shiftView(dir) {
        if (monthMode) view.y += dir;
        else {
            const d = new Date(view.y, view.m + dir, 1);
            view = { y: d.getFullYear(), m: d.getMonth() };
        }
        render();
    }

    function position() {
        wrap.classList.remove('dp-open-up');
        const rect = trigger.getBoundingClientRect();
        const below = window.innerHeight - rect.bottom;
        if (below < panel.offsetHeight + 16 && rect.top > below) wrap.classList.add('dp-open-up');
    }

    function open() {
        if (input.disabled || isOpen()) return;
        const p = parse(input.value);
        const base = p || parse(monthMode ? todayIso.slice(0, 7) : todayIso);
        view = { y: base.y, m: base.m };
        cursor = input.value || null;
        render();
        wrap.classList.add('dp-open');
        position();
    }

    function close() {
        if (!isOpen()) return;
        wrap.classList.remove('dp-open');
        cursor = null;
    }

    function pick(iso) {
        if (iso && !inRange(iso)) return;
        valueDescriptor.set.call(input, iso);
        syncTrigger();
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        close();
    }

    // Arrows move the cursor by day (month mode: month) and week/row vertically; Enter picks.
    function moveCursor(step) {
        const p = parse(cursor || input.value) || parse(monthMode ? todayIso.slice(0, 7) : todayIso);
        const d = monthMode ? new Date(p.y, p.m + step, 1) : new Date(p.y, p.m, p.d + step);
        cursor = toIso(d.getFullYear(), d.getMonth(), d.getDate());
        view = { y: d.getFullYear(), m: d.getMonth() };
        render();
    }

    const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(input, 'value', {
        configurable: true,
        enumerable: true,
        get() { return valueDescriptor.get.call(input); },
        set(v) {
            valueDescriptor.set.call(input, v);
            syncTrigger();
            if (isOpen()) render();
        },
    });

    new MutationObserver(() => { syncTrigger(); if (isOpen()) render(); })
        .observe(input, { attributes: true, attributeFilter: ['min', 'max', 'disabled', 'required', 'placeholder'] });

    input.addEventListener('focus', () => wrap.classList.add('dp-focus'));
    input.addEventListener('blur', () => { wrap.classList.remove('dp-focus'); close(); });
    input.addEventListener('change', syncTrigger);
    // Typing into a hidden date field would edit it blind, so keys go through the panel.
    input.addEventListener('keydown', (e) => {
        if (input.disabled || e.key === 'Tab') { close(); return; }
        e.preventDefault();
        if (!isOpen()) {
            if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(e.key)) open();
            return;
        }
        const rowStep = monthMode ? 3 : 7;
        if (e.key === 'Escape') close();
        else if (e.key === 'ArrowLeft') moveCursor(-1);
        else if (e.key === 'ArrowRight') moveCursor(1);
        else if (e.key === 'ArrowUp') moveCursor(-rowStep);
        else if (e.key === 'ArrowDown') moveCursor(rowStep);
        else if (e.key === 'PageUp') shiftView(-1);
        else if (e.key === 'PageDown') shiftView(1);
        else if ((e.key === 'Enter' || e.key === ' ') && cursor) pick(cursor);
    });

    trigger.addEventListener('mousedown', (e) => e.preventDefault());
    trigger.addEventListener('click', () => {
        if (input.disabled) return;
        input.focus({ preventScroll: true });
        if (isOpen()) close(); else open();
    });
    document.addEventListener('click', (e) => { if (isOpen() && !wrap.contains(e.target)) close(); });
    window.addEventListener('resize', () => { if (isOpen()) position(); });

    syncTrigger();
}


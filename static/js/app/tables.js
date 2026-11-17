// Dispatch warnings, smart tables and lists
// (Part of the app's main script; loaded in order by base.html.)

function initDispatchQtyWarnings() {
    document.querySelectorAll('.dispatch-qty-input').forEach((input) => {
        const requested = parseInt(input.dataset.requestedQty, 10);
        const form = input.closest('form');
        if (Number.isNaN(requested)) return;

        function sync() {
            const val = parseInt(input.value, 10);
            const overLimit = !Number.isNaN(val) && val > requested;
            const differs = !Number.isNaN(val) && val !== requested && !overLimit;
            input.classList.toggle('dispatch-qty-invalid', overLimit);
            input.classList.toggle('dispatch-qty-diff', differs);
            input.setCustomValidity(overLimit ? `Can't dispatch more than the ${requested} requested.` : '');
        }

        input.addEventListener('input', sync);
        sync();

        if (form) {
            form.addEventListener('submit', (e) => {
                if (form.dataset.dispatchConfirmed === '1') { delete form.dataset.dispatchConfirmed; return; }
                const val = parseInt(input.value, 10);

                if (!Number.isNaN(val) && val > requested) {
                    e.preventDefault();
                    input.reportValidity();
                    return;
                }

                if (Number.isNaN(val) || val === requested) return;

                const message = `This branch requested ${requested}. You're about to dispatch ${val} instead, which is less than what was asked for.`;
                if (!window.appConfirm) {
                    if (!confirm(message + ' Continue?')) e.preventDefault();
                    return;
                }
                e.preventDefault();
                e.stopImmediatePropagation();
                const submitter = e.submitter || null;
                window.appConfirm({ title: 'Dispatch less than requested?', detail: message, ok: 'Dispatch ' + val, tone: 'accent' })
                    .then((ok) => {
                        if (!ok) return;
                        form.dataset.dispatchConfirmed = '1';
                        if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
                        else form.submit();
                    });
            });
        }
    });
}

// Anchors a paginated table/list's own top edge to the same scroll
// position on every Prev/Next click, instead of leaving it to chance.
// Without this: paging to a much shorter page (e.g. a last page with
// one leftover row) shrinks the document underneath an unmoved scroll
// position, so the browser clamps scrollTop to the new, shorter max —
// which happens to land right back on the now-nearby pagination
// controls. Paging back to a taller page doesn't reverse that:
// scrollTop stays exactly where it was clamped to, now sitting
// somewhere mid-table on the taller page, with its rows and controls
// pushed down out of view below it — the user has to scroll down again
// to find them. Scrolling the container's top into view on every click
// (not just when it happens to be needed) keeps the anchor point the
// same regardless of which direction the row count just changed.
function scrollPaginatedContainerIntoView(container) {
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    container.scrollIntoView({ behavior, block: 'start' });
}

// Opt-in: data-fit-to="<css selector>" on a [data-smart-table] makes the card
// that holds it exactly as tall as the card the selector finds (the page's own
// CSS stretches the list card to that row height), shows as many rows per page
// as fit in that height, and shares the leftover pixels between the rows so
// the table ends flush with the pagination bar. It re-fits when the reference
// card resizes (a form that grows or shrinks) and on window resize. Under the
// two-column breakpoint it goes back to the table's own data-page-size.
function setupFitToSibling(container, getRows, setRows) {
    const refSel = container.dataset.fitTo;
    const card = container.closest('.card');
    const ref = refSel && card && card.parentElement
        ? (document.querySelector(refSel) || null) : null;
    const table = container.querySelector('.smart-table');
    if (!ref || !card || !table) return;

    const baseRows = getRows();
    const wide = window.matchMedia('(min-width: 64.0625rem)');
    let queued = false;

    function fit() {
        if (!container.isConnected) { stop(); return; }
        if (!wide.matches) {
            container.style.removeProperty('--fit-row-h');
            if (getRows() !== baseRows) setRows(baseRows);
            return;
        }
        const tbody = table.querySelector('tbody');
        const rows = Array.prototype.filter.call(tbody.rows, (r) => r.style.display !== 'none');
        if (!rows.length) return;
        // Natural row height, measured without the previous fit's stretch.
        container.style.removeProperty('--fit-row-h');
        const rowH = rows.reduce((sum, r) => sum + r.offsetHeight, 0) / rows.length;
        const h = (sel) => { const el = sel instanceof Element ? sel : card.querySelector(sel); return el ? el.offsetHeight : 0; };
        const pag = container.querySelector('.smart-pagination');
        const pagH = (pag && pag.offsetHeight) || 62;
        const avail = card.clientHeight - h('.card-header') - h(container.querySelector('.table-toolbar'))
            - h(table.querySelector('thead')) - pagH - h('.pg-foot');
        const n = Math.max(5, Math.floor(avail / rowH));
        if (n !== getRows()) setRows(n);
        if (avail > 0) container.style.setProperty('--fit-row-h', Math.max(rowH, avail / n) + 'px');
    }

    function schedule() {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; fit(); });
    }

    let ro = null;
    function stop() {
        if (ro) ro.disconnect();
        window.removeEventListener('resize', schedule);
    }
    if (typeof ResizeObserver === 'function') {
        ro = new ResizeObserver(schedule);
        ro.observe(ref);
        // The fixed parts around the rows (title, search bar, pagination,
        // footnote) change height when text wraps differently or a web font
        // finishes loading; each of those needs a re-fit too.
        ['.card-header', '.table-toolbar', '.smart-pagination', '.pg-foot'].forEach((sel) => {
            const el = card.querySelector(sel);
            if (el) ro.observe(el);
        });
    }
    window.addEventListener('resize', schedule);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
    schedule();
}

// ---- Click-to-sort column headers, for every table that can take it. ----
// A header becomes a button; click cycles ascending, descending, back to the
// original order (aria-sort says which). The type of a column is read from its
// own cells: all numbers (₱1,200.50, 45%, 500 mL, +12) sort numerically, all
// dates by time, anything else as text with embedded numbers in order. Empty
// and "None"-style cells always sink to the bottom. A table is left alone when
// it can't be sorted safely: a cell spans columns, a row edits inputs, or the
// header and rows don't line up. Opt out with data-no-sort on the table.
const SORT_BLANK = /^(|none|n\/a|not costed|-|—|–)$/i;
const SORT_NUMBER = /^[+\-−]?\d*\.?\d+(?:e\d+)?\s*(%|ml|l|g|kg|u|x|pcs?|bottles?)?$/i;
const SORT_LEADING = /^[+\-−]?\d*\.?\d+(?!\d*-\d)\s*[a-z%\/]/i;
const SORT_DATE = /\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|\b\d{4}-\d{2}-\d{2}\b/i;

function sortCellText(cell) {
    if (!cell) return '';
    if (cell.dataset.sortValue !== undefined) return cell.dataset.sortValue;
    return cell.textContent.replace(/\s+/g, ' ').trim();
}

function sortColumnKind(values) {
    const filled = values.filter((v) => !SORT_BLANK.test(v));
    if (!filled.length) return 'text';
    const clean = (v) => v.replace(/[₱$,\s]/g, (m) => (m === ' ' ? ' ' : '')).replace('−', '-').trim();
    if (filled.every((v) => SORT_NUMBER.test(clean(v)))) return 'number';
    if (filled.every((v) => SORT_DATE.test(v) && !Number.isNaN(Date.parse(v.replace(/\s+/g, ' '))))) return 'date';
    // "₱0.13 / milliliter", "20 liter", "85 units": a number up front, then a unit.
    if (filled.every((v) => SORT_LEADING.test(clean(v)))) return 'leading';
    return 'text';
}

function sortValueOf(text, kind) {
    if (SORT_BLANK.test(text)) return null;
    if (kind === 'number' || kind === 'leading') return parseFloat(text.replace(/[₱$,\s]/g, '').replace('−', '-'));
    if (kind === 'date') return Date.parse(text.replace(/\s+/g, ' '));
    return text;
}

function makeSortable(table, rowsOf, applyOrder) {
    if (!table || table.dataset.sortReady || table.hasAttribute('data-no-sort')) return;
    const thead = table.tHead;
    const tbody = table.tBodies[0];
    if (!thead || !tbody || thead.rows.length !== 1) return;
    const heads = Array.prototype.slice.call(thead.rows[0].cells);
    const rows = rowsOf();
    if (rows.length < 3) return;
    if (table.querySelector('[colspan], [rowspan]')) return;
    if (tbody.querySelector('input, select, textarea')) return;
    if (rows.some((r) => r.cells.length !== heads.length)) return;
    table.dataset.sortReady = '1';

    const original = rows.slice();
    let active = null;
    let dir = 0;

    heads.forEach((th, col) => {
        const label = th.textContent.replace(/\s+/g, ' ').trim();
        if (!label || th.querySelector('button, input, select, a')) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'th-sort';
        btn.append(label);
        const caret = document.createElement('span');
        caret.className = 'th-caret';
        caret.setAttribute('aria-hidden', 'true');
        btn.appendChild(caret);
        th.replaceChildren(btn);
        th.classList.add('is-sortable');
        btn.addEventListener('click', () => {
            dir = active === th ? (dir === 1 ? -1 : (dir === -1 ? 0 : 1)) : 1;
            active = dir === 0 ? null : th;
            heads.forEach((h) => {
                if (h === th && dir !== 0) h.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
                else h.removeAttribute('aria-sort');
            });
            let ordered;
            if (dir === 0) {
                ordered = original.slice();
            } else {
                const cells = original.map((r) => sortCellText(r.cells[col]));
                const kind = sortColumnKind(cells);
                const keyed = original.map((r, i) => ({ r, i, v: sortValueOf(cells[i], kind) }));
                keyed.sort((a, b) => {
                    if (a.v === null && b.v === null) return a.i - b.i;
                    if (a.v === null) return 1;
                    if (b.v === null) return -1;
                    const c = kind === 'text'
                        ? a.v.localeCompare(b.v, undefined, { numeric: true, sensitivity: 'base' })
                        : a.v - b.v;
                    return (c * dir) || (a.i - b.i);
                });
                ordered = keyed.map((k) => k.r);
            }
            ordered.forEach((r) => tbody.appendChild(r));
            applyOrder(ordered);
        });
    });
}

// Plain (non-paginated) tables: dashboards, detail pages, short lists.
function initPlainTableSort() {
    document.querySelectorAll('.table-wrap table').forEach((table) => {
        if (table.closest('[data-smart-table]')) return;
        const tbody = table.tBodies[0];
        if (!tbody) return;
        makeSortable(table, () => Array.prototype.slice.call(tbody.rows), () => {});
    });
}

// A scrolling table region has to be reachable from the keyboard, or a person
// who can't use a mouse can't scroll it. Only the ones that really scroll.
function initScrollableTableRegions() {
    document.querySelectorAll('.table-wrap').forEach((wrap) => {
        const scrolls = wrap.scrollHeight > wrap.clientHeight + 1 || wrap.scrollWidth > wrap.clientWidth + 1;
        if (scrolls && !wrap.hasAttribute('tabindex')) {
            wrap.tabIndex = 0;
            wrap.setAttribute('role', 'region');
            const heading = wrap.closest('.card') && wrap.closest('.card').querySelector('h2');
            wrap.setAttribute('aria-label', (heading ? heading.textContent.trim() + ' table' : 'Table') + ', scrollable');
        }
    });
}

function initSmartTables() {
    document.querySelectorAll('[data-smart-table]').forEach((container) => {
        const input = container.querySelector('.smart-search-input');

        const filterSelect = container.querySelector('.smart-filter-select');
        const filterKey = filterSelect ? (filterSelect.dataset.filterKey || 'filter') : null;
        const table = container.querySelector('.smart-table');
        if (!table) return;

        const tbody = table.querySelector('tbody');
        let rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        const noMatch = container.querySelector('.smart-no-match');
        const pagination = container.querySelector('.smart-pagination');
        const pageInfo = container.querySelector('.smart-page-info');
        const prevBtn = container.querySelector('[data-page-prev]');
        const nextBtn = container.querySelector('[data-page-next]');
        const countTarget = container.dataset.countTarget ? document.getElementById(container.dataset.countTarget) : null;
        const countLabel = container.dataset.countLabel || 'row';
        let pageSize = parseInt(container.dataset.pageSize, 10) || 10;

        const rowText = new Map();
        rows.forEach((r) => {
            const visible = r.textContent.replace(/\s+/g, ' ').trim().toLowerCase();
            const extra = (r.dataset.search || '').toLowerCase();
            rowText.set(r, extra && extra !== visible ? visible + ' ' + extra : visible);
        });

        // Opt-in (table-collapsible, see style.css's @media (max-width:
        // 680px) rules) — each row's mobile card starts collapsed to
        // just its .mobile-inline-anchor/.mobile-inline-action, with a
        // "Details" bar at the bottom to reveal its .mobile-detail
        // cells (Qty/Price/Total/Payment on Record Sale's recent-sales
        // list) instead of dumping all of them at once.
        if (table.classList.contains('table-collapsible')) {
            rows.forEach((row) => {
                const toggleBtn = row.querySelector('.row-toggle-btn');
                if (!toggleBtn) return;
                toggleBtn.addEventListener('click', () => {
                    const expanded = row.classList.toggle('row-expanded');
                    toggleBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                });
            });
        }

        let filtered = rows.slice();
        let page = 1;

        // "Showing 1 to 8 of 31" at the left of the pagination bar.
        let rangeEl = null;
        if (pagination) {
            rangeEl = document.createElement('span');
            rangeEl.className = 'smart-range';
            pagination.insertBefore(rangeEl, pagination.firstChild);
        }
        // An empty search result offers the way out, not just the bad news.
        let clearBtn = null;
        if (noMatch && input) {
            clearBtn = document.createElement('button');
            clearBtn.type = 'button';
            clearBtn.className = 'btn btn-ghost btn-sm smart-clear';
            clearBtn.textContent = 'Clear search';
            clearBtn.hidden = true;
            noMatch.appendChild(clearBtn);
            clearBtn.addEventListener('click', () => {
                input.value = '';
                page = 1;
                render();
                input.focus();
            });
        }

        function render() {
            const q = input ? input.value.trim().toLowerCase() : '';
            const fval = filterSelect ? filterSelect.value : '';
            filtered = rows.filter((r) => {
                // Plain substring first (cheap, and what people expect for
                // exact bits like an OR# or phone), then the shared smart
                // matcher: words in any order and one small typo.
                const text = rowText.get(r);
                const matchesSearch = !q || text.indexOf(q) !== -1
                    || (window.SmartMatch ? window.SmartMatch.score(q, text, '') !== -1 : false);
                const matchesFilter = !fval || (r.dataset[filterKey] || '') === fval;
                return matchesSearch && matchesFilter;
            });

            const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
            if (page > totalPages) page = totalPages;

            rows.forEach((r) => { r.style.display = 'none'; });
            const start = (page - 1) * pageSize;
            filtered.slice(start, start + pageSize).forEach((r) => { r.style.display = ''; });

            table.style.display = filtered.length ? '' : 'none';
            if (noMatch) noMatch.style.display = filtered.length ? 'none' : '';
            if (clearBtn) clearBtn.hidden = !(input && input.value.trim());

            if (pagination) {
                pagination.style.display = filtered.length > pageSize ? 'flex' : 'none';
                if (pageInfo) pageInfo.textContent = 'Page ' + page + ' of ' + totalPages;
                if (rangeEl) {
                    const from = filtered.length ? start + 1 : 0;
                    const to = Math.min(start + pageSize, filtered.length);
                    rangeEl.textContent = 'Showing ' + from + ' to ' + to + ' of ' + filtered.length;
                }
                if (prevBtn) prevBtn.disabled = page <= 1;
                if (nextBtn) nextBtn.disabled = page >= totalPages;
            }

            if (countTarget) {
                // data-count-distinct: count unique row data-count-key
                // values instead of rows (e.g. Products counts SKUs by
                // base code, so A1-85ML and A1-BULK are one).
                const count = 'countDistinct' in container.dataset
                    ? new Set(filtered.map((r) => r.dataset.countKey || r)).size
                    : filtered.length;
                countTarget.textContent = count + ' ' + countLabel + (count !== 1 ? 's' : '');
            }
        }

        if (input) {
            input.addEventListener('input', () => { page = 1; render(); });
        }
        if (filterSelect) {
            filterSelect.addEventListener('change', () => { page = 1; render(); });
        }
        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (page > 1) { page--; render(); scrollPaginatedContainerIntoView(container); }
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
                if (page < totalPages) { page++; render(); scrollPaginatedContainerIntoView(container); }
            });
        }

        // A page can change data-page-size (e.g. Products fits as many rows
        // as the card beside it is tall) and fire this to re-paginate.
        container.addEventListener('smart-table-repage', () => {
            pageSize = parseInt(container.dataset.pageSize, 10) || 10;
            render();
        });

        render();
        makeSortable(table, () => rows, (ordered) => { rows = ordered.slice(); page = 1; render(); });
        setupFitToSibling(container, () => pageSize, (n) => { pageSize = n; render(); });
    });
    initPlainTableSort();
    initScrollableTableRegions();
}

// Pagination-only counterpart to initSmartTables() for pages that show a
// paginated list of cards/blocks rather than a <table> — e.g. a partner's
// inquiry history, or the shipment blocks on Receive Stock. There's no
// search here on purpose (see the two call sites): each item's content
// isn't a flat row of short cell text, so a text search box would be
// awkward to use, and pagination alone already caps how much renders at
// once. Container needs data-smart-list, data-page-size, and
// data-list-item-selector (the CSS selector for one "item" inside it);
// pagination controls follow the same .smart-pagination markup used by
// initSmartTables() so the two share styling.
function initSmartLists() {
    document.querySelectorAll('[data-smart-list]').forEach((container) => {
        const itemSelector = container.dataset.listItemSelector || '.smart-list-item';
        const items = Array.prototype.slice.call(container.querySelectorAll(itemSelector));
        const pagination = container.querySelector('.smart-pagination');
        if (!items.length) {
            if (pagination) pagination.style.display = 'none';
            return;
        }

        const pageSize = parseInt(container.dataset.pageSize, 10) || 10;
        const pageInfo = container.querySelector('.smart-page-info');
        const prevBtn = container.querySelector('[data-page-prev]');
        const nextBtn = container.querySelector('[data-page-next]');
        const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
        let page = 1;

        function render() {
            const start = (page - 1) * pageSize;
            items.forEach((el, i) => {
                el.style.display = (i >= start && i < start + pageSize) ? '' : 'none';
            });
            if (pagination) {
                pagination.style.display = items.length > pageSize ? 'flex' : 'none';
                if (pageInfo) pageInfo.textContent = 'Page ' + page + ' of ' + totalPages;
                if (prevBtn) prevBtn.disabled = page <= 1;
                if (nextBtn) nextBtn.disabled = page >= totalPages;
            }
        }

        if (prevBtn) {
            prevBtn.addEventListener('click', () => {
                if (page > 1) { page--; render(); scrollPaginatedContainerIntoView(container); }
            });
        }
        if (nextBtn) {
            nextBtn.addEventListener('click', () => {
                if (page < totalPages) { page++; render(); scrollPaginatedContainerIntoView(container); }
            });
        }

        render();
    });
}


// An empty state's action can click a button that is already on the page
// ("New sale", "+ Add material"): <button data-click-target="openNewSaleBtn">.
// Delegated on document, so it survives soft navigation and re-rendered lists.
document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-click-target]');
    if (!btn) return;
    const target = document.getElementById(btn.dataset.clickTarget);
    if (target) target.click();
});

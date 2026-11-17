// Chart.js defaults and chart helpers (legends, KPI tiles, formatters)
// (Part of the app's main script; loaded in order by base.html.)

function sortMovementTypes(types) {
    function rank(t) {
        var i = MOVEMENT_TYPE_ORDER.indexOf(t);
        return i === -1 ? MOVEMENT_TYPE_ORDER.length : i;
    }
    return types.slice().sort(function (a, b) { return rank(a) - rank(b); });
}

function titleCaseLabel(text) {
    return String(text).toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
}

// HTML legend for a Chart.js chart, rendered into `container`. Chart.js's
// canvas legend wraps into ragged centred rows on a phone and eats into
// the plot's fixed height; this one is a real grid (see .chart-legend in
// style.css) under the canvas. Each chip still toggles its dataset on and
// off, same as clicking the built-in legend.
function renderChartLegend(chart, container) {
    if (!container) return;
    container.innerHTML = '';
    chart.data.datasets.forEach(function (ds, i) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'chart-legend-item';
        btn.setAttribute('aria-pressed', 'true');
        var swatch = document.createElement('span');
        swatch.className = 'chart-legend-swatch';
        swatch.style.background = ds.borderColor;
        var label = document.createElement('span');
        label.className = 'chart-legend-label';
        label.textContent = ds.label;
        btn.appendChild(swatch);
        btn.appendChild(label);
        btn.addEventListener('click', function () {
            var visible = chart.isDatasetVisible(i);
            chart.setDatasetVisibility(i, !visible);
            btn.setAttribute('aria-pressed', String(!visible));
            chart.update();
        });
        container.appendChild(btn);
    });
}

// ---- Dashboard chart kit (Boss-Insights style) -------------------------
// Shared by the admin and branch Reports pages: compact peso formatting,
// SVG sparklines for KPI tiles, up/down delta chips, a donut with its total
// in the middle plus a "name / amount / share" list, and one set of Chart.js
// defaults so every graph reads as one system.
function compactPeso(value) {
    var n = Number(value) || 0;
    var abs = Math.abs(n);
    var sign = n < 0 ? '-' : '';
    if (abs < 1000) return sign + '₱' + abs.toLocaleString('en-PH', { maximumFractionDigits: abs % 1 ? 2 : 0 });
    var f = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 });
    return sign + '₱' + f.format(abs);
}

function fullPeso(value) {
    return '₱' + Number(value || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pctChange(current, previous) {
    if (!previous) return null;
    return ((current - previous) / Math.abs(previous)) * 100;
}

// Chart.js cannot parse every modern CSS colour (oklch, color-mix), so each
// theme token is resolved to plain sRGB through a 1px canvas first.
var chartColorCtx = null;
function chartRgb(value) {
    if (!chartColorCtx) {
        var cv = document.createElement('canvas');
        cv.width = cv.height = 1;
        chartColorCtx = cv.getContext('2d', { willReadFrequently: true });
    }
    chartColorCtx.clearRect(0, 0, 1, 1);
    chartColorCtx.fillStyle = '#000';
    chartColorCtx.fillStyle = value;
    chartColorCtx.fillRect(0, 0, 1, 1);
    var d = chartColorCtx.getImageData(0, 0, 1, 1).data;
    return d[3] === 255 ? 'rgb(' + d[0] + ',' + d[1] + ',' + d[2] + ')'
        : 'rgba(' + d[0] + ',' + d[1] + ',' + d[2] + ',' + (d[3] / 255).toFixed(3) + ')';
}

// Chart lines, bars and dots must stand out from the surface they sit on (3:1,
// the usual minimum for graphics). A theme's data colours are free to be pale:
// Cyberpunk's neon yellow, or the gold, measured 1.3-2:1 on a light page. This
// deepens such a colour towards black (light surface) or white (dark surface)
// just far enough to pass, and leaves colours that already pass untouched.
function chartRgbParts(s) {
    var m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?/.exec(String(s));
    return m ? { c: [+m[1], +m[2], +m[3]], a: m[4] === undefined ? 1 : +m[4] } : null;
}

function chartLuminance(c) {
    var f = function (v) { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}

function chartContrast(a, b) {
    var la = chartLuminance(a), lb = chartLuminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function ensureChartContrast(color, surface, min) {
    var c = chartRgbParts(color), b = chartRgbParts(surface);
    if (!c || !b || c.a < 1 || chartContrast(c.c, b.c) >= min) return color;
    var target = chartLuminance(b.c) > 0.4 ? [0, 0, 0] : [255, 255, 255];
    for (var t = 0.05; t <= 1.001; t += 0.05) {
        var mixed = c.c.map(function (v, i) { return Math.round(v + (target[i] - v) * t); });
        if (chartContrast(mixed, b.c) >= min) return 'rgb(' + mixed.join(',') + ')';
    }
    return 'rgb(' + target.join(',') + ')';
}

function chartTheme() {
    var css = getComputedStyle(document.documentElement);
    var dark = document.documentElement.getAttribute('data-theme') === 'dark';
    var tok = function (name) { return chartRgb(css.getPropertyValue(name).trim()); };
    var surface = tok('--surface');
    // What a chart is really drawn on: the first opaque background behind a chart
    // card or KPI tile (some styles paint these a different colour from --surface,
    // and the glass styles make the surface translucent), else the page background.
    var under = tok('--bg');
    var probe = document.querySelector('.kpi-spark, .chart-canvas-wrap, .donut-wrap, .kpi-tile, .chart-card, .card');
    for (var el = probe; el; el = el.parentElement) {
        var paint = chartRgbParts(getComputedStyle(el).backgroundColor);
        if (paint && paint.a >= 1) { under = 'rgb(' + paint.c.join(',') + ')'; break; }
    }
    var vis = function (name) { return ensureChartContrast(tok(name), under, 3); };
    var accent = vis('--accent');
    var sales = vis('--chart-sales');
    var cream = vis('--chart-cream');
    var cost = vis('--chart-cost');
    return {
        dark: dark,
        ink: tok('--ink'),
        soft: tok('--ink-soft'),
        faint: tok('--ink-faint'),
        grid: tok('--border'),
        surface: surface,
        tipEdge: tok('--chart-tip-edge'),
        font: css.getPropertyValue('--font').trim(),
        accent: accent,
        sales: sales,
        cost: cost,
        profit: accent,
        cream: cream,
        series: [accent, sales, cream, cost, vis('--chart-4'), vis('--chart-5'), vis('--chart-6'), vis('--chart-7')]
    };
}

// Chart.js defaults + the "total in the middle" doughnut plugin. Safe to
// call on every data load.
function applyChartDefaults() {
    var t = chartTheme();
    Chart.defaults.font.family = t.font;
    Chart.defaults.font.size = 12;
    Chart.defaults.color = t.soft;
    Chart.defaults.borderColor = t.grid;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.elements.line.borderWidth = 2.5;
    Chart.defaults.elements.line.tension = 0.4;
    Chart.defaults.elements.point.radius = 0;
    Chart.defaults.elements.point.hoverRadius = 5;
    Chart.defaults.elements.bar.borderRadius = 6;
    Chart.defaults.elements.bar.borderSkipped = false;
    Chart.defaults.interaction = { mode: 'index', intersect: false };
    var tip = Chart.defaults.plugins.tooltip;
    tip.backgroundColor = t.surface;
    tip.titleColor = t.ink;
    tip.bodyColor = t.soft;
    tip.borderColor = t.tipEdge;
    tip.borderWidth = 1;
    tip.padding = 10;
    tip.cornerRadius = 10;
    tip.boxPadding = 4;
    tip.usePointStyle = true;
    if (!Chart.registry.plugins.get('donutCenter')) {
        Chart.register({
            id: 'donutCenter',
            afterDraw: function (chart) {
                var opts = chart.options.plugins && chart.options.plugins.donutCenter;
                if (!opts || !opts.title) return;
                var meta = chart.getDatasetMeta(0);
                if (!meta || !meta.data.length) return;
                var ctx = chart.ctx;
                var x = meta.data[0].x, y = meta.data[0].y;
                var th = chartTheme();
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = th.ink;
                ctx.font = "700 22px 'Inter', sans-serif";
                ctx.fillText(opts.title, x, y - 8);
                ctx.fillStyle = th.faint;
                ctx.font = "500 12px 'Inter', sans-serif";
                ctx.fillText(opts.subtitle || 'Total', x, y + 14);
                ctx.restore();
            }
        });
    }
}

// Flat sparkline with a soft fill, as inline SVG (no axes, no library).
function sparklineSVG(values, color) {
    var w = 100, h = 30, pad = 2;
    if (!values || values.length < 2) values = [0, 0];
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
        var x = (i / (values.length - 1)) * w;
        var y = h - pad - ((v - min) / span) * (h - pad * 2);
        return x.toFixed(2) + ',' + y.toFixed(2);
    });
    var line = 'M' + pts.join(' L');
    return '<svg class="kpi-spark-svg" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" aria-hidden="true">' +
        '<path d="' + line + ' L' + w + ',' + h + ' L0,' + h + ' Z" fill="' + color + '" opacity="0.14"/>' +
        '<path d="' + line + '" fill="none" stroke="' + color + '" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>' +
        '</svg>';
}

// Fills one KPI tile (markup: .kpi-tile > .kpi-value, .kpi-meta, .kpi-spark).
//   o.value      text for the big number (title gets o.title)
//   o.delta      change vs previous period, or null (a % unless o.points)
//   o.points     true when delta is in percentage points (margin tiles)
//   o.goodWhenUp false for costs: an increase is shown red
//   o.note       text beside the chip, e.g. "17.1% of total sales"
//   o.series     values for the sparkline; o.color its colour
function fillKpiTile(el, o) {
    if (!el) return;
    var val = el.querySelector('.kpi-value');
    val.textContent = o.value;
    if (o.title) val.title = o.title;
    var meta = el.querySelector('.kpi-meta');
    var chip = '';
    var d = o.delta;
    if (d !== null && d !== undefined && isFinite(d)) {
        var up = d > 0, flat = Math.abs(d) < 0.05;
        var good = flat ? null : (up === (o.goodWhenUp !== false));
        var cls = flat ? 'flat' : (good ? 'good' : 'bad');
        var arrow = flat ? '→' : (up ? '↑' : '↓');
        chip = '<span class="kpi-chip kpi-chip-' + cls + '" title="vs previous period">' + arrow + ' ' +
            Math.abs(d).toFixed(1) + (o.points ? ' pts' : '%') + '</span>';
    }
    meta.innerHTML = chip;
    if (o.note) {
        var note = document.createElement('span');
        note.className = 'kpi-note';
        note.textContent = o.note;
        meta.appendChild(note);
    }
    var spark = el.querySelector('.kpi-spark');
    if (spark) spark.innerHTML = o.series ? sparklineSVG(o.series, o.color) : '';
}

// "Dot  Name  Amount  Share%" rows under a doughnut.
function renderShareList(container, items, colors, formatValue) {
    if (!container) return;
    var total = items.reduce(function (a, b) { return a + b.value; }, 0);
    container.innerHTML = '';
    if (!items.length) {
        container.innerHTML = '<div class="share-empty">No data yet</div>';
        return;
    }
    items.forEach(function (it, i) {
        var row = document.createElement('div');
        row.className = 'share-row';
        var dot = document.createElement('span');
        dot.className = 'share-dot';
        dot.style.background = colors[i % colors.length];
        var name = document.createElement('span');
        name.className = 'share-name';
        name.textContent = it.name;
        var amt = document.createElement('span');
        amt.className = 'share-amount';
        amt.textContent = formatValue(it.value);
        var pct = document.createElement('span');
        pct.className = 'share-pct';
        pct.textContent = (total ? (it.value / total) * 100 : 0).toFixed(1) + '%';
        row.appendChild(dot); row.appendChild(name); row.appendChild(amt); row.appendChild(pct);
        container.appendChild(row);
    });
}

// Common options for a smooth line chart.
function lineChartOptions(t, yFormat) {
    return {
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (c) { return ' ' + c.dataset.label + '  ' + (yFormat ? yFormat(c.parsed.y) : c.parsed.y); } } }
        },
        scales: {
            y: { beginAtZero: true, border: { display: false }, grid: { color: t.grid }, ticks: { padding: 8, callback: function (v) { return yFormat ? yFormat(v) : v; } } },
            x: { border: { display: false }, grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 18 } }
        }
    };
}

// Keeps a donut readable with many outlets: the biggest (max - 1) rows stay,
// the rest fold into one "Other (n)" slice, flagged other:true so callers can
// give it a neutral colour instead of recycling a palette colour.
function topWithOther(rows, max) {
    var sorted = rows.slice().sort(function (a, b) { return b.value - a.value; });
    if (sorted.length <= max) return sorted;
    var head = sorted.slice(0, max - 1);
    var tail = sorted.slice(max - 1);
    head.push({
        name: 'Other (' + tail.length + ')',
        value: tail.reduce(function (a, r) { return a + r.value; }, 0),
        other: true
    });
    return head;
}

// One colour per row: palette order, with the folded "Other" slice neutral.
function rowColors(rows, palette, neutral) {
    return rows.map(function (r, i) { return r.other ? neutral : palette[i % palette.length]; });
}

function shortDateLabel(iso) {
    var d = new Date(iso + 'T00:00:00');
    return isNaN(d) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}


// ---- Empty charts ---------------------------------------------------------
// A chart with nothing to plot (no sales yet, every value zero) draws bare
// axes, which reads as broken. Call syncChartEmpties() after the charts are
// built: any canvas whose chart has no positive value gets a message in its
// place, and gets its chart back the moment data arrives.
var CHART_EMPTY_COPY = {
    profitChart: ['No sales in this window', 'Sales, cost and profit are charted once sales are recorded in the selected period.'],
    salesTrendChart: ['No sales in this window', 'Your sales are charted here once they are recorded in the selected period.'],
    channelChart: ['No sales yet', 'Sales are split by channel as soon as the first one is recorded.'],
    saleTypeChart: ['No sales yet', 'Sales are split by type as soon as the first one is recorded.'],
    variantChart: ['No bottles sold yet', 'Units are split by variant once sales are recorded.'],
    paymentChart: ['No payments yet', 'Payment methods are compared once sales are recorded.'],
    branchSalesChart: ['No branch sales yet', 'Units sold per branch are charted once branches record sales.'],
    capitalChart: ['Nothing to compare yet', 'Capital, materials bought and revenue appear once you log purchases, production or sales.'],
    topProductsChart: ['No product sales yet', 'Your best sellers are ranked here once sales are recorded.'],
    stockChart: ['No stock on hand', 'Stock levels are charted once products are received at your branch.'],
    trendChart: ['No ledger movement in this window', 'Production, sales and refills are charted once they are recorded in the selected period.'],
    bpRevenueChart: ['No branch revenue yet', 'Each branch’s share of revenue appears once sales are recorded.'],
    bpUnitsChart: ['No bottles sold yet', 'Branches are ranked here once they record sales.']
};

function chartHasData(chart) {
    return chart.data.datasets.some(function (ds) {
        return (ds.data || []).some(function (v) {
            var n = (v && typeof v === 'object') ? Number(v.y !== undefined ? v.y : v.x) : Number(v);
            return n > 0;
        });
    });
}

function syncChartEmpties(root) {
    if (typeof Chart === 'undefined') return;
    (root || document).querySelectorAll('canvas').forEach(function (canvas) {
        var chart = Chart.getChart(canvas);
        if (!chart) return;
        var wrap = canvas.parentElement;
        var note = wrap.querySelector(':scope > .chart-empty');
        var empty = !chartHasData(chart);
        if (empty && !note) {
            var copy = CHART_EMPTY_COPY[canvas.id] || ['No data yet', 'This fills in as activity is recorded.'];
            note = document.createElement('div');
            note.className = 'chart-empty';
            note.setAttribute('role', 'status');
            var icon = document.createElement('span');
            icon.className = 'empty-icon';
            icon.setAttribute('aria-hidden', 'true');
            icon.innerHTML = '<svg viewBox="0 0 24 24" focusable="false"><path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/></svg>';
            var text = document.createElement('div');
            text.className = 'empty-text';
            var title = document.createElement('p');
            title.className = 'empty-title';
            title.textContent = copy[0];
            var body = document.createElement('p');
            body.className = 'empty-body';
            body.textContent = copy[1];
            text.appendChild(title); text.appendChild(body);
            note.appendChild(icon); note.appendChild(text);
            wrap.appendChild(note);
        }
        if (!empty && note) note.remove();
        canvas.style.visibility = empty ? 'hidden' : '';
    });
}

// ---- Re-theme live ---------------------------------------------------------
// Charts, sparklines and legend dots are painted with plain colour values read
// from the theme once, when they are built. Switching light/dark, the interface
// style or the accent colour afterwards left them in the old palette (pale lines
// on a light page, dark axis text on a dark one) until the page was reloaded.
// This watches for those switches and swaps the old palette for the new one in
// place: no reload, no new request.
var chartPalette = null;

function chartPaletteOf(t) {
    var p = { ink: t.ink, soft: t.soft, faint: t.faint, grid: t.grid, surface: t.surface, tipEdge: t.tipEdge,
        accent: t.accent, sales: t.sales, cost: t.cost, cream: t.cream };
    t.series.forEach(function (c, i) { p['series' + i] = c; });
    return p;
}

function chartColorKey(s) { return String(s).replace(/\s+/g, ''); }

function remapChartColors(value, map, seen) {
    if (typeof value === 'string') {
        var hit = map[chartColorKey(value)];
        return hit === undefined ? value : hit;
    }
    if (!value || typeof value !== 'object') return value;
    seen = seen || [];
    if (seen.indexOf(value) !== -1) return value;
    seen.push(value);
    if (Array.isArray(value)) {
        for (var i = 0; i < value.length; i++) value[i] = remapChartColors(value[i], map, seen);
        return value;
    }
    // Chart.js builds its option objects without a normal prototype, so test what the
    // object is rather than what it inherits from.
    if (typeof Node !== 'undefined' && value instanceof Node) return value;
    if (value instanceof Date || typeof value.getContext === 'function') return value;
    Object.keys(value).forEach(function (k) {
        if (k === 'data' || k === 'labels') return;
        value[k] = remapChartColors(value[k], map, seen);
    });
    return value;
}

function remapInlineColors(text, map) {
    return text.replace(/rgba?\([^)]*\)/g, function (m) {
        var hit = map[chartColorKey(m)];
        return hit === undefined ? m : hit;
    });
}

function rethemeCharts() {
    if (!chartPalette) return;
    var next = chartPaletteOf(chartTheme());
    var map = {};
    var changed = false;
    Object.keys(next).forEach(function (k) {
        if (chartPalette[k] !== next[k]) {
            changed = true;
            if (map[chartColorKey(chartPalette[k])] === undefined) map[chartColorKey(chartPalette[k])] = next[k];
        }
    });
    if (!changed) return;
    chartPalette = next;

    if (typeof Chart !== 'undefined') {
        applyChartDefaults();
        var seen = {};
        document.querySelectorAll('canvas').forEach(function (canvas) {
            var chart = Chart.getChart(canvas);
            if (!chart || seen[chart.id]) return;
            seen[chart.id] = true;
            remapChartColors(chart.config.data, map);
            remapChartColors(chart.config.options, map);
            chart.update('none');
        });
    }
    // Sparklines (stroke / fill attributes) and anything coloured with an inline style
    // from a theme colour, e.g. the dots beside a doughnut's legend.
    document.querySelectorAll('.kpi-spark-svg *').forEach(function (el) {
        ['stroke', 'fill'].forEach(function (attr) {
            var v = el.getAttribute(attr);
            if (v) el.setAttribute(attr, remapInlineColors(v, map));
        });
    });
    document.querySelectorAll('.content [style*="rgb"]').forEach(function (el) {
        el.setAttribute('style', remapInlineColors(el.getAttribute('style'), map));
    });
}

function initChartRetheme() {
    if (initChartRetheme.done || typeof MutationObserver !== 'function') return;
    initChartRetheme.done = true;
    try { chartPalette = chartPaletteOf(chartTheme()); } catch (e) { return; }
    var timer = null;
    new MutationObserver(function () {
        clearTimeout(timer);
        timer = setTimeout(rethemeCharts, 60);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-style', 'style'] });
}
initChartRetheme();

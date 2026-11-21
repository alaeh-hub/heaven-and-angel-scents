// Shared smart matching for every dropdown/autocomplete (window.SmartMatch); loads
// before form-controls.js.
//   - ignores case, accents and punctuation ("A1-85ML" == "a1 85ml")
//   - every word must match, in any order ("red lacoste" finds "Lacoste Red")
//   - words match at a word start, inside a word, or across a SKU without dashes
//   - one small typo is forgiven on words of 4+ letters
// score() returns a number where LOWER IS BETTER, or -1 for no match.
(function () {
    'use strict';

    function norm(s) {
        return String(s == null ? '' : s)
            .normalize('NFD').replace(/[̀-ͯ]/g, '')
            .toLowerCase();
    }

    function words(s) {
        return norm(s).split(/[^a-z0-9]+/).filter(Boolean);
    }

    // Edit distance, bailing out once it is certain to exceed `max`.
    function lev(a, b, max) {
        if (Math.abs(a.length - b.length) > max) return max + 1;
        let prev = [];
        for (let j = 0; j <= b.length; j++) prev[j] = j;
        for (let i = 1; i <= a.length; i++) {
            const cur = [i];
            let rowMin = i;
            for (let j = 1; j <= b.length; j++) {
                const cost = a[i - 1] === b[j - 1] ? 0 : 1;
                cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
                if (cur[j] < rowMin) rowMin = cur[j];
            }
            if (rowMin > max) return max + 1;
            prev = cur;
        }
        return prev[b.length];
    }

    function tokenScore(tok, ws, compact) {
        let best = Infinity;
        for (let i = 0; i < ws.length; i++) {
            const w = ws[i];
            if (w === tok) return i * 0.01;
            if (w.indexOf(tok) === 0) best = Math.min(best, 1 + i * 0.01);
            else if (w.indexOf(tok) !== -1) best = Math.min(best, 3 + i * 0.01);
        }
        if (best === Infinity && compact.indexOf(tok) !== -1) best = 4;
        if (best === Infinity && tok.length >= 4) {
            const max = tok.length >= 8 ? 2 : 1;
            for (let i = 0; i < ws.length; i++) {
                const w = ws[i];
                const d = Math.min(lev(tok, w, max), lev(tok, w.slice(0, tok.length), max));
                if (d <= max) best = Math.min(best, 5 + d);
            }
        }
        return best;
    }

    // text: the main label. extra: anything else worth searching (SKU, size...).
    function score(query, text, extra) {
        const q = norm(query).trim();
        if (!q) return 0;
        const toks = words(q);
        if (!toks.length) return 0;
        const label = norm(text);
        const ws = words(label + ' ' + (extra == null ? '' : extra));
        const compact = ws.join('');
        let total = 0;
        for (let i = 0; i < toks.length; i++) {
            const s = tokenScore(toks[i], ws, compact);
            if (s === Infinity) return -1;
            total += s;
        }
        // Whole-label ranking so exact/prefix hits lead; scores stay >= 0 (-1 means no match).
        const flat = label.trim();
        if (flat === q) return total / toks.length;
        if (flat.indexOf(q) === 0) return 3 + total / toks.length;
        return 6 + total / toks.length;
    }

    // Filter + rank: getText(item) -> label, getExtra(item) -> extra text. Stable order.
    function rank(items, query, getText, getExtra) {
        if (!norm(query).trim()) return items.slice();
        return items
            .map(function (it, i) {
                return { it: it, i: i, s: score(query, getText(it), getExtra ? getExtra(it) : '') };
            })
            .filter(function (r) { return r.s !== -1; })
            .sort(function (a, b) { return a.s - b.s || a.i - b.i; })
            .map(function (r) { return r.it; });
    }

    // Wraps hit parts of `text` in <span class=cls> as DOM nodes (never innerHTML).
    function highlight(text, query, cls) {
        const frag = document.createDocumentFragment();
        const raw = String(text == null ? '' : text);
        const toks = words(query).sort(function (a, b) { return b.length - a.length; });
        if (!toks.length) { frag.appendChild(document.createTextNode(raw)); return frag; }
        // Match on the lowercase accent-stripped copy; highlight only when lengths line up (NFD).
        const flat = norm(raw);
        if (flat.length !== raw.length) { frag.appendChild(document.createTextNode(raw)); return frag; }
        const hit = new Array(raw.length).fill(false);
        toks.forEach(function (t) {
            let from = 0;
            for (;;) {
                const at = flat.indexOf(t, from);
                if (at === -1) break;
                for (let k = at; k < at + t.length; k++) hit[k] = true;
                from = at + t.length;
            }
        });
        let i = 0;
        while (i < raw.length) {
            let j = i;
            while (j < raw.length && hit[j] === hit[i]) j++;
            const piece = raw.slice(i, j);
            if (hit[i]) {
                const span = document.createElement('span');
                span.className = cls || 'ac-match';
                span.textContent = piece;
                frag.appendChild(span);
            } else {
                frag.appendChild(document.createTextNode(piece));
            }
            i = j;
        }
        return frag;
    }

    window.SmartMatch = { norm: norm, score: score, rank: rank, highlight: highlight };
})();

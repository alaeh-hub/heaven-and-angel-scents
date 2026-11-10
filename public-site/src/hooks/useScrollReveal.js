import { useEffect } from 'react';

/**
 * Selector -> entrance variant. Everything here is revealed the first time
 * it scrolls into view. Elements that already animate themselves (package
 * cards, steps, catalog tiles, anything wrapped in <Reveal>) are left out.
 * Variants are defined in styles/reveal.css.
 */
const TARGETS = [
  ['.section-head', 'up'],
  ['.section-head > *', 'up'],
  ['.scent-rail-head', 'up'],
  ['.word-rail', 'fade'],
  ['.pkg-filter', 'up'],
  ['.stats-item', 'up'],
  ['.earn-row', 'left'],
  ['.earn-note', 'fade'],
  ['.about-body > *', 'up'],
  ['.about-start', 'up'],
  ['.craft-frame', 'scale'],
  ['.collection-ctas', 'up'],
  ['.journey-step', 'up'],
  ['.journey-step > *', 'up'],
  ['.faq-item', 'up'],
  ['.faq-more', 'fade'],
  ['.closing-direct', 'up'],
  ['.closing-channel', 'scale'],
  ['.closing-note', 'fade'],
  ['.footer-brand', 'up'],
  ['.footer-col', 'up'],
  ['.footer-legal', 'fade'],
  ['.detail-topbar', 'fade'],
  ['.catalog-foot', 'up'],
];
const SELECTOR = TARGETS.map(([s]) => s).join(',');
const VARIANT = TARGETS.map(([s, v]) => [s, v]);

/**
 * One app-wide IntersectionObserver + MutationObserver (no per-component
 * code). New nodes (the packages arrive after a fetch, pages swap) are
 * tagged `data-sr="hide"` before paint, then flipped to `data-sr="in"` the
 * first time they are 12% visible; siblings stagger via --sr-i. Visitors
 * who ask for reduced motion get nothing tagged, so nothing ever hides.
 */
export function useScrollReveal() {
  useEffect(() => {
    if (!('IntersectionObserver' in window)) return undefined;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        io.unobserve(el);
        el.dataset.sr = 'in';
        el.addEventListener('animationend', (e) => {
          if (e.target !== el) return;
          delete el.dataset.sr;
          delete el.dataset.srv;
        });
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });

    const tag = (el) => {
      if (el.dataset.sr || el.closest('[data-no-reveal]')) return;
      const hit = VARIANT.find(([s]) => el.matches(s));
      el.dataset.srv = hit ? hit[1] : 'up';
      el.dataset.sr = 'hide';
      const sibs = el.parentElement ? [...el.parentElement.children].filter((c) => c.matches(SELECTOR)) : [el];
      el.style.setProperty('--sr-i', String(Math.min(sibs.indexOf(el), 6)));
      io.observe(el);
    };

    const scan = (root) => {
      if (root.nodeType !== 1) return;
      if (root.matches(SELECTOR)) tag(root);
      root.querySelectorAll(SELECTOR).forEach(tag);
    };

    scan(document.body);
    const mo = new MutationObserver((records) => {
      records.forEach((r) => r.addedNodes.forEach(scan));
    });
    mo.observe(document.body, { childList: true, subtree: true });
    return () => { mo.disconnect(); io.disconnect(); };
  }, []);
}

import { useEffect } from 'react';

const SELECTOR = '.glow-card, .glow-area';

/**
 * One app-wide pointer listener that sets --mx/--my on `.glow-card` and
 * `.glow-area` under the mouse (glow is CSS, see tokens.css). Batched
 * per frame; mouse only.
 */
export function useGlowPointer() {
  useEffect(() => {
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return undefined;
    let frame = 0;
    let target = null;
    let x = 0;
    let y = 0;

    const apply = () => {
      frame = 0;
      let el = target instanceof Element ? target.closest(SELECTOR) : null;
      while (el) {
        const rect = el.getBoundingClientRect();
        el.style.setProperty('--mx', `${(x - rect.left).toFixed(1)}px`);
        el.style.setProperty('--my', `${(y - rect.top).toFixed(1)}px`);
        el = el.parentElement ? el.parentElement.closest(SELECTOR) : null;
      }
    };

    const onMove = (e) => {
      if (e.pointerType !== 'mouse') return;
      target = e.target;
      x = e.clientX;
      y = e.clientY;
      if (!frame) frame = requestAnimationFrame(apply);
    };

    document.addEventListener('pointermove', onMove, { passive: true });
    return () => {
      document.removeEventListener('pointermove', onMove);
      cancelAnimationFrame(frame);
    };
  }, []);
}

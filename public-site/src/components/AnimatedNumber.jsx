import { useEffect, useRef } from 'react';
import { animate, motion, useInView, useMotionValue, useReducedMotion, useTransform } from 'motion/react';
import { EASE_OUT } from '../motion.js';
import { peso } from '../utils.js';

/**
 * A number that rolls to its value (optionally `from` a start, `whenInView`
 * once). Motion-value driven, so no re-renders; screen readers and reduced
 * motion get the final figure.
 */
export default function AnimatedNumber({ value, from, format = peso, whenInView = false, duration = 1.1, className }) {
  const ref = useRef(null);
  const reduce = useReducedMotion();
  const inView = useInView(ref, { once: true, amount: 0.6 });
  const mv = useMotionValue(reduce ? value : (from ?? value));
  const text = useTransform(mv, (v) => format(v));

  const lastChange = useRef(0);

  useEffect(() => {
    if (reduce) {
      mv.set(value);
      return undefined;
    }
    if (whenInView && !inView) return undefined;
    // Rapid changes (slider drags) follow exactly; only a lone change rolls.
    const now = performance.now();
    const rapid = now - lastChange.current < 250;
    lastChange.current = now;
    if (rapid) {
      mv.set(value);
      return undefined;
    }
    const controls = animate(mv, value, { duration, ease: EASE_OUT });
    return () => controls.stop();
  }, [value, inView, whenInView, reduce, duration, mv]);

  return (
    <span ref={ref} className={className}>
      <span className="sr-only">{format(value)}</span>
      <motion.span aria-hidden="true">{text}</motion.span>
    </span>
  );
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate, useParams } from 'react-router';
import { AnimatePresence, motion, useDragControls } from 'motion/react';
import { XIcon } from '@phosphor-icons/react';
import CustomScrollbar from '../components/CustomScrollbar.jsx';
import PackageDetail from '../components/PackageDetail.jsx';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock.js';
import { EASE_OUT } from '../motion.js';

const DISMISS_PX = 140;
const DISMISS_VELOCITY = 700;

/**
 * Transform-origin for the sheet to grow from the clicked card's centre
 * (`state.origin`). Mirrors `.sheet` in detail.css (max 1180px, 40px top
 * gap, 12px on phones). Null without an origin, where the sheet rises.
 */
function growFrom(origin) {
  if (!origin) return null;
  const width = Math.min(1180, window.innerWidth);
  const top = window.matchMedia('(max-width: 720px)').matches ? 12 : 40;
  const x = origin.x - (window.innerWidth - width) / 2;
  const y = origin.y - top;
  return `${Math.round(x)}px ${Math.round(y)}px`;
}

/**
 * The package detail as a sheet over the list, rendered by App.jsx when a
 * package URL carries a `background` location (see PackageCard); the URL
 * stays shareable. Grows from the card when opened from one. Closes via
 * X, Escape, backdrop tap or dragging the top bar; the route changes after
 * the exit animation.
 */
export default function DetailDialog() {
  const { slug, packageId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);
  const dragControls = useDragControls();
  const scrollRef = useRef(null);
  // Read once: only the opening grows out of the card.
  const [transformOrigin] = useState(() => growFrom(location.state?.origin));
  useBodyScrollLock(true);

  // Another package picked from inside the sheet: start it at the top.
  useEffect(() => {
    scrollRef.current?.scrollTo(0, 0);
  }, [packageId]);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && close();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);

  const leave = () => {
    // Opening pushed one entry; with nothing to go back to, replace it with the list.
    const { background } = location.state;
    if (window.history.state?.idx > 0) navigate(-1);
    else navigate(background.pathname + background.search, { replace: true });
  };

  return createPortal(
    <AnimatePresence onExitComplete={leave}>
      {open && (
        <motion.div
          key="scrim"
          className="sheet-scrim"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.35, ease: 'easeOut' }}
          onClick={close}
        />
      )}
      {open && (
        <motion.div
          key="sheet"
          className="sheet"
          role="dialog"
          aria-modal="true"
          aria-label="Package details"
          style={{ ...(transformOrigin ? { transformOrigin } : null), willChange: 'transform, opacity' }}
          // Calm rise: short ease-out on transform and opacity (a full-screen spring looked rough).
          initial={{ y: 56, scale: transformOrigin ? 0.94 : 0.985, opacity: 0 }}
          animate={{
            y: 0,
            scale: 1,
            opacity: 1,
            transition: { duration: 0.5, ease: EASE_OUT, opacity: { duration: 0.28, ease: 'easeOut' } },
          }}
          exit={{ y: '100%', opacity: 0.6, transition: { duration: 0.38, ease: [0.5, 0, 0.75, 0] } }}
          drag="y"
          dragControls={dragControls}
          dragListener={false}
          dragConstraints={{ top: 0, bottom: 0 }}
          dragElastic={{ top: 0, bottom: 0.7 }}
          onDragEnd={(_, info) => {
            if (info.offset.y > DISMISS_PX || info.velocity.y > DISMISS_VELOCITY) close();
          }}
        >
          <div className="sheet-bar" onPointerDown={(e) => dragControls.start(e)}>
            <span className="sheet-grabber" aria-hidden="true" />
            <button
              type="button"
              className="icon-btn"
              aria-label="Close"
              onClick={close}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <XIcon size={16} weight="bold" />
            </button>
          </div>
          <div className="sheet-body">
            <div className="sheet-scroll" ref={scrollRef}>
              <PackageDetail
                slug={slug}
                packageId={packageId}
                inDialog
                linkState={location.state}
                onMissing={close}
              />
            </div>
            <CustomScrollbar target={scrollRef} />
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

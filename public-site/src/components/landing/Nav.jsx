import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion, useMotionValueEvent, useScroll } from 'motion/react';
import { ArrowUpRightIcon, EnvelopeSimpleIcon, PhoneIcon } from '@phosphor-icons/react';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock.js';
import { SPRING } from '../../motion.js';
import { scrollToSection } from '../../utils.js';
import { useSite } from '../InquiryProvider.jsx';

export const NAV_LINKS = [
  { id: 'packages', label: 'Packages' },
  { id: 'earnings', label: 'Earnings' },
  { id: 'shop-online', label: 'Shop now' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'about', label: 'About' },
  { id: 'faq', label: 'FAQ' },
];

/**
 * Which of `ids` crosses the viewport middle. `lockRef.current` pauses
 * tracking during a nav-click scroll so the pill goes straight to its link.
 */
function useActiveSection(ids, lockRef) {
  const [active, setActive] = useState(null);
  useEffect(() => {
    // A set, so leaving into a section not in the nav clears the highlight.
    const inBand = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) inBand.add(entry.target.id);
        else inBand.delete(entry.target.id);
      });
      if (!lockRef.current) setActive(ids.find((id) => inBand.has(id)) ?? null);
    }, { rootMargin: '-45% 0px -50% 0px' });
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [ids, lockRef]);
  return [active, setActive];
}


/** Pixels of upward scrolling it takes to bring the hidden bar back. */
const UP_TO_SHOW = 24;
/** Without `scrollend`: a glide is finished once the page is still this long (ms). */
const SETTLE_MS = 180;
/** Unlock regardless after this long (ms), in case no end is ever reported. */
const GLIDE_MAX_MS = 4000;

/**
 * Sticky top bar: transparent over the hero, frosted on scroll, hides when
 * reading down and returns on a real scroll up.
 */
export default function Nav({ hiddenIds = [] }) {
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [hovered, setHovered] = useState(null);
  const navigatingRef = useRef(false);
  const { site, openInquiry } = useSite();
  const contact = site?.contact || {};
  // Absent sections (e.g. Earnings without discounts) get no dead link.
  const hiddenKey = hiddenIds.join(' ');
  const links = useMemo(() => NAV_LINKS.filter((l) => !hiddenKey.split(' ').includes(l.id)), [hiddenKey]);
  // New list when a section appears or goes, so the tracker re-observes.
  const sectionIds = useMemo(() => links.map((l) => l.id), [links]);
  const [active, setActive] = useActiveSection(sectionIds, navigatingRef);

  // How far the page has moved up since it last moved down.
  const upTravel = useRef(0);

  useMotionValueEvent(scrollY, 'change', (y) => {
    const dy = y - (scrollY.getPrevious() ?? 0);
    setScrolled(y > 8);
    if (y <= 320 || menuOpen) {
      upTravel.current = 0;
      setHidden(false);
      return;
    }
    if (dy > 0) {
      upTravel.current = 0;
      // Stay put while a nav click is carrying the page downward.
      if (!navigatingRef.current) setHidden(true);
    } else if (dy < 0) {
      // Only a deliberate scroll up shows it (momentum tails used to pop it back).
      upTravel.current -= dy;
      if (upTravel.current > UP_TO_SHOW) setHidden(false);
    }
  });

  // Ends a nav glide: stops watching the scroll and lets the bar react again.
  const stopWatch = useRef(() => {});
  useEffect(() => () => stopWatch.current(), []);

  /** Light the chosen link at once, then glide to its section. */
  const goTo = (e, id) => {
    // Lock first so the bar stays put; unlock when settled (glides vary in length).
    stopWatch.current();
    navigatingRef.current = true;
    if (!scrollToSection(id)) {
      navigatingRef.current = false;
      return;
    }
    e.preventDefault();
    setActive(id);
    // `scrollend` marks the end; older browsers wait for SETTLE_MS of stillness.
    const hasEnd = 'onscrollend' in window;
    let timer;
    const done = () => stopWatch.current();
    const settle = () => {
      clearTimeout(timer);
      timer = setTimeout(done, SETTLE_MS);
    };
    const cap = setTimeout(done, GLIDE_MAX_MS);
    stopWatch.current = () => {
      clearTimeout(timer);
      clearTimeout(cap);
      window.removeEventListener('scroll', settle);
      window.removeEventListener('scrollend', done);
      navigatingRef.current = false;
      stopWatch.current = () => {};
    };
    if (hasEnd) {
      window.addEventListener('scrollend', done, { once: true });
    } else {
      window.addEventListener('scroll', settle, { passive: true });
      settle();
    }
  };

  useBodyScrollLock(menuOpen);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const mq = window.matchMedia('(min-width: 961px)');
    const close = () => mq.matches && setMenuOpen(false);
    mq.addEventListener('change', close);
    return () => mq.removeEventListener('change', close);
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKey = (e) => e.key === 'Escape' && setMenuOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [menuOpen]);

  return (
    <>
      <motion.header
        className="nav"
        data-scrolled={scrolled || menuOpen}
        animate={{ y: hidden ? '-100%' : '0%' }}
        transition={SPRING}
      >
        <div className="container nav-inner">
          <a className="nav-brand" href="#top" aria-label="Heaven & Angel Scents, back to top">
            <img src="/static/img/new-wordmark-sm.webp" alt="" />
          </a>

          <nav className="nav-links" aria-label="Sections" onMouseLeave={() => setHovered(null)} onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); e.currentTarget.style.setProperty('--spot-x', `${e.clientX - r.left}px`); }}>
            {links.map(({ id, label }) => (
              <motion.a
                key={id}
                href={`#${id}`}
                className="nav-link"
                aria-current={active === id}
                onClick={(e) => goTo(e, id)}
                onMouseEnter={() => setHovered(id)}
                onFocus={() => setHovered(id)}
                onBlur={() => setHovered(null)}
                whileTap={{ scale: 0.94 }}
                transition={SPRING}
              >
                {/* Faint hover pill glides between links; the active pill springs over. */}
                <AnimatePresence>
                  {hovered === id && active !== id && (
                    <motion.span
                      layoutId="nav-hover"
                      className="nav-link-hover"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={SPRING}
                    />
                  )}
                </AnimatePresence>
                {active === id && <motion.span layoutId="nav-active" className="nav-link-pill" transition={SPRING} />}
                {label}
              </motion.a>
            ))}
          </nav>

          <div className="nav-actions">
            {/* Packages has its own link, so this button is for getting in touch. */}
            <button type="button" className="btn btn-primary btn-sm" onClick={() => openInquiry()}>Send an inquiry</button>
            <button
              type="button"
              className="nav-menu-btn"
              aria-label={menuOpen ? 'Close menu' : 'Open menu'}
              aria-expanded={menuOpen}
              aria-controls="mobile-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              {/* Two lines that cross into an X, so open and close read as one motion. */}
              <span className="burger" aria-hidden="true"><i /><i /></span>
            </button>
          </div>
        </div>
      </motion.header>

      <AnimatePresence>
        {menuOpen && (
          <motion.div
            id="mobile-menu"
            className="mobile-menu"
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
          >
            <nav className="mobile-menu-links" aria-label="Sections">
              {links.map(({ id, label }, i) => (
                <motion.a
                  key={id}
                  href={`#${id}`}
                  aria-current={active === id}
                  onClick={(e) => { setMenuOpen(false); goTo(e, id); }}
                  initial={{ opacity: 0, y: 14 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ ...SPRING, delay: 0.05 + 0.045 * i }}
                >
                  <span className="mm-n" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                  <span className="mm-label">{label}</span>
                  <ArrowUpRightIcon className="mm-arrow" size={18} weight="bold" aria-hidden="true" />
                </motion.a>
              ))}
            </nav>

            <motion.div
              className="mobile-menu-foot"
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ ...SPRING, delay: 0.1 + 0.045 * links.length }}
            >
              <button type="button" className="btn btn-primary" onClick={() => { setMenuOpen(false); openInquiry(); }}>
                Send an inquiry
              </button>
              {(contact.phone || contact.email) && (
                <div className="mobile-menu-contact">
                  {contact.phone && (
                    <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`}><PhoneIcon size={16} weight="duotone" aria-hidden="true" />{contact.phone}</a>
                  )}
                  {contact.email && (
                    <a href={`mailto:${contact.email}`}><EnvelopeSimpleIcon size={16} weight="duotone" aria-hidden="true" />{contact.email}</a>
                  )}
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

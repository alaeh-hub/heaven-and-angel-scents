import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon } from '@phosphor-icons/react';
import { productsPath } from '../../utils.js';
import { SCENTS, SCENT_ASPECT, SCENT_BOTTLE_CENTRE } from '../../content/scents.js';
import Chapter from '../Chapter.jsx';
import Reveal from '../Reveal.jsx';
import DotField from './DotField.jsx';

/** Alternates women's and men's scents along the shelf; the larger side fills the tail. */
function interleave(scents) {
  const her = scents.filter((s) => s.variant === 'Female');
  const him = scents.filter((s) => s.variant === 'Male');
  const out = [];
  for (let i = 0; i < Math.max(her.length, him.length); i += 1) {
    if (her[i]) out.push(her[i]);
    if (him[i]) out.push(him[i]);
  }
  return out;
}

const SHELF = interleave(SCENTS);

/** Frame sized from the render's proportions and bottle centre line (CSS parks and slides it). */
export const SHELF_VARS = {
  '--scent-aspect': SCENT_ASPECT,
  '--scent-centre': `${(SCENT_BOTTLE_CENTRE * 100).toFixed(2)}%`,
  '--tiles': SHELF.length,
};

/**
 * One scent tile: bottle with its gift box behind. Hover, focus or tap
 * slides the box out and shows the name (layers share one render canvas).
 */
export function ScentTile({ scent, hidden, active, auto, eager, nearby }) {
  const [open, setOpen] = useState(false);
  const forHim = scent.variant === 'Male';

  const toggleOnTouch = (e) => {
    // Mouse uses hover alone; touch and pen have no hover, so a tap flips it.
    if (e.pointerType === 'mouse') return;
    // The swipe row opens the centred tile; a tap just centres the tapped one.
    if (auto) {
      e.currentTarget.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      return;
    }
    setOpen((v) => !v);
  };

  return (
    <div
      className="bottle-tile glow-card"
      data-variant={scent.variant}
      data-code={scent.code}
      data-open={open || active || undefined}
      tabIndex={hidden ? undefined : 0}
      role={hidden ? undefined : 'group'}
      aria-label={hidden ? undefined : `${scent.name} ${scent.code}, ${forHim ? 'for him' : 'for her'}`}
      onPointerEnter={(e) => e.pointerType === 'mouse' && setOpen(true)}
      onPointerLeave={(e) => e.pointerType === 'mouse' && setOpen(false)}
      onPointerUp={toggleOnTouch}
      onFocus={(e) => e.currentTarget.matches(':focus-visible') && setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span className="bottle-tile-aura" aria-hidden="true" />
      <div className="scent-frame">
        <span className="scent-floor" aria-hidden="true" />
        <img
          className="scent-box"
          src={scent.box}
          alt=""
          loading={nearby ? 'eager' : 'lazy'}
          decoding="async"
          draggable={false}
        />
        <img
          className="scent-bottle"
          src={scent.bottle}
          alt={hidden ? '' : `${scent.name} ${scent.code}, 85 ml eau de parfum`}
          loading={eager ? 'eager' : 'lazy'}
          decoding="async"
          draggable={false}
        />
      </div>
      {/* Shown with the box on hover; always shown without hover. */}
      <span className="bottle-tile-meta" aria-hidden="true">
        <span className="bottle-tile-code">{scent.code}</span>
        <span className="bottle-tile-name">{scent.name}</span>
        <span className="bottle-tile-sub">{forHim ? 'For him' : 'For her'} · 85 ml</span>
      </span>
    </div>
  );
}

/**
 * The page's one marquee: a slow shelf of bottles that pauses on hover.
 * On touch or reduced motion it is a swipe row (tap shows the box).
 * Sits over its own DotField, lit only within this section.
 */
export default function Collection() {
  const { slug } = useParams();
  const sectionRef = useRef(null);
  // Code of the tile centred in the swipe row (touch screens only).
  const [centred, setCentred] = useState(null);

  // Pause the CSS animation off screen so the compositor stops ticking.
  useEffect(() => {
    const marquee = sectionRef.current?.querySelector('.marquee');
    if (!marquee) return undefined;
    const io = new IntersectionObserver(([entry]) => {
      marquee.dataset.paused = entry.isIntersecting ? 'false' : 'true';
    });
    io.observe(marquee);
    return () => io.disconnect();
  }, []);

  // Touch: the tile nearest the swipe row centre opens its box.
  useEffect(() => {
    const marquee = sectionRef.current?.querySelector('.marquee');
    const track = marquee?.querySelector('.marquee-track');
    if (!track) return undefined;
    const touch = window.matchMedia('(hover: none)');
    let raf = 0;
    const pick = () => {
      raf = 0;
      if (!touch.matches) { setCentred(null); return; }
      const box = marquee.getBoundingClientRect();
      const mid = box.left + box.width / 2;
      let best = null;
      let bestGap = Infinity;
      for (const tile of track.children) {
        const r = tile.getBoundingClientRect();
        const gap = Math.abs(r.left + r.width / 2 - mid);
        if (gap < bestGap) { bestGap = gap; best = tile.dataset.code; }
      }
      setCentred(best);
    };
    const schedule = () => { if (!raf) raf = requestAnimationFrame(pick); };
    marquee.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    touch.addEventListener('change', schedule);
    pick();
    return () => {
      cancelAnimationFrame(raf);
      marquee.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      touch.removeEventListener('change', schedule);
    };
  }, []);

  // Preload boxes of the centred tile and neighbours so they are ready.
  const centredAt = SHELF.findIndex((s) => s.code === centred);

  return (
    <section className="section collection" id="collection" ref={sectionRef}>
      <DotField className="collection-dots" />
      <div className="container">
        <Chapter n={5} label="Collection" title="The collection." lede="Men’s and women’s signatures, each bottled at 85 ml.">
          <div className="collection-ctas">
            <motion.div whileTap={{ scale: 0.97 }}>
              <Link to={productsPath(slug)} className="btn btn-secondary">
                View all products <ArrowRightIcon size={16} weight="bold" />
              </Link>
            </motion.div>
            {/* Grouped so the pair wraps together. */}
            <div className="collection-cta-links">
              <Link to={`${productsPath(slug)}?gender=Male`} className="link-arrow">
                For him <ArrowRightIcon size={16} weight="bold" />
              </Link>
              <Link to={`${productsPath(slug)}?gender=Female`} className="link-arrow">
                For her <ArrowRightIcon size={16} weight="bold" />
              </Link>
            </div>
          </div>
        </Chapter>
      </div>
      <Reveal className="marquee" y={0} style={SHELF_VARS}>
        {[0, 1].map((copy) => (
          <div key={copy} className="marquee-track" aria-hidden={copy === 1 ? 'true' : undefined}>
            {SHELF.map((scent, i) => (
              <ScentTile
                key={scent.code}
                scent={scent}
                hidden={copy === 1}
                auto={copy === 0}
                active={copy === 0 && centred === scent.code}
                eager={copy === 0 && i < 4}
                nearby={copy === 0 && centredAt >= 0 && Math.abs(i - centredAt) <= 3}
              />
            ))}
          </div>
        ))}
      </Reveal>
    </section>
  );
}

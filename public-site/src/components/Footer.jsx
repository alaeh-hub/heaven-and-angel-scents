import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { motion, useReducedMotion, useScroll, useTransform } from 'motion/react';
import {
  EnvelopeSimpleIcon, MapPinIcon, PhoneIcon, ShoppingBagIcon, ShoppingCartIcon,
  StorefrontIcon,
} from '@phosphor-icons/react';
import { FOOTER } from '../content/footer.js';
import { packagesPath, productsPath, scrollToSection } from '../utils.js';
import { UNDERLINE_HOVER } from '@/components/ui/skiper-ui/skiper40';
import { FaFacebookF, FaFacebookMessenger, FaInstagram, FaTiktok, FaXTwitter, FaYoutube } from 'react-icons/fa6';
import { useSite } from './InquiryProvider.jsx';

const SOCIAL_ICONS = {
  facebook: FaFacebookF,
  instagram: FaInstagram,
  tiktok: FaTiktok,
  youtube: FaYoutube,
  messenger: FaFacebookMessenger,
  x: FaXTwitter,
};

// No official marketplace marks exist, so generic storefront icons get brand tints.
const MARKETPLACE_ICONS = {
  shopee: ShoppingBagIcon,
  lazada: ShoppingCartIcon,
  tiktokshop: StorefrontIcon,
};

const MARKETPLACE_COLORS = {
  shopee: '#EE4D2D',
  lazada: '#0F146D',
  tiktokshop: '#FE2C55',
};

const EXPLORE = [
  { id: 'packages', label: 'Packages' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'about', label: 'About the brand' },
  { id: 'faq', label: 'FAQ' },
];

/** A footer taller than this share of the screen is not revealed (top would hide). */
const MAX_REVEAL_SHARE = 0.9;

const clamp01 = (v) => Math.min(1, Math.max(0, v));
/** 0..1 across [from, to] of `v`. */
const span = (v, from, to) => clamp01((v - from) / (to - from));

/**
 * Sticky-reveal footer: it waits under the page (z-index -1) while the
 * content in `children` slides up to uncover it, fading/rising in step.
 * `children` must be opaque (see .footer-zone in landing.css). Reduced
 * motion or a footer taller than the screen gives a plain footer.
 */
export default function Footer({ children }) {
  const { slug } = useParams();
  const { site } = useSite();
  const reduce = useReducedMotion();
  const footerRef = useRef(null);
  const coverRef = useRef(null);
  // Kept in a ref: read by the scroll mapping without re-rendering.
  const sizesRef = useRef({ footer: 1, viewport: 1 });
  const [fits, setFits] = useState(true);

  useEffect(() => {
    const footer = footerRef.current;
    if (!footer) return undefined;
    const check = () => {
      sizesRef.current = { footer: footer.offsetHeight || 1, viewport: window.innerHeight || 1 };
      setFits(footer.offsetHeight <= window.innerHeight * MAX_REVEAL_SHARE);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(footer);
    window.addEventListener('resize', check);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', check);
    };
  }, []);

  const reveal = fits && !reduce;

  // Uncovered amount 0..1, measured off the cover (a stuck sticky box moves).
  const { scrollYProgress } = useScroll({ target: coverRef, offset: ['end end', 'end start'] });
  const uncovered = useTransform(scrollYProgress, (v) => {
    const { footer, viewport } = sizesRef.current;
    return clamp01((v * viewport) / footer);
  });
  // Mapped via functions so Motion avoids the native scroll timeline (hero useSpan issue).
  const opacity = useTransform(uncovered, (v) => span(v, 0, 0.7));
  const scale = useTransform(uncovered, (v) => 0.94 + 0.06 * v);
  const y = useTransform(uncovered, (v) => 48 * (1 - v));

  const phone = site?.contact?.phone || FOOTER.phone;
  const email = site?.contact?.email || FOOTER.email;

  /** Router link to the landing section (a plain anchor broke on trailing slashes). */
  const sectionLink = ({ id, label }) => (
    <Link
      className={UNDERLINE_HOVER}
      to={`${packagesPath(slug)}#${id}`}
      onClick={(e) => { if (scrollToSection(id)) e.preventDefault(); }}
    >
      {label}
    </Link>
  );

  return (
    <div className="footer-zone">
      <div className="footer-cover" ref={coverRef}>{children}</div>
      <footer ref={footerRef} className={`footer${reveal ? ' footer-reveal' : ''}`}>
        {/* Keyed by mode so a switch remounts and drops reveal values. */}
        <motion.div
          key={reveal ? 'reveal' : 'static'}
          className="footer-body"
          style={reveal ? { opacity, scale, y } : undefined}
        >
          <svg className="footer-mega" viewBox="0 0 1500 250" aria-hidden="true" focusable="false">
            <defs>
              <linearGradient id="footer-mega-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#d9b64b" stopOpacity="0.1" />
                <stop offset="1" stopColor="#d9b64b" stopOpacity="0.025" />
              </linearGradient>
            </defs>
            <text x="750" y="200" textAnchor="middle">Heaven &amp; Angel</text>
          </svg>
          <div className="container">
            <div className="footer-grid">
              <div className="footer-brand">
                <img src="/static/img/new-wordmark-sm.webp" alt="Heaven & Angel Scents" />
                <p>{FOOTER.blurb}</p>
                {FOOTER.socials.length > 0 && (
                  <ul className="footer-socials" aria-label="Follow us">
                    {FOOTER.socials.map(({ network, label, href }) => {
                      const Icon = SOCIAL_ICONS[network];
                      if (!Icon) return null;
                      return (
                        <li key={network}>
                          <motion.a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={label}
                            title={label}
                            whileHover={{ y: -3 }}
                            whileTap={{ scale: 0.92 }}
                          >
                            <Icon size={17} aria-hidden="true" />
                          </motion.a>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              <nav className="footer-col" aria-label="Explore">
                <h2>Explore</h2>
                <ul>
                  {EXPLORE.map((item) => <li key={item.id}>{sectionLink(item)}</li>)}
                  <li><Link className={UNDERLINE_HOVER} to={productsPath(slug)}>All products</Link></li>
                </ul>
              </nav>

              {FOOTER.marketplaces?.length > 0 && (
                <nav className="footer-col" aria-label="Shop online">
                  <h2>Shop online</h2>
                  <ul className="footer-marketplaces">
                    {FOOTER.marketplaces.map(({ network, label, href }) => {
                      const Icon = MARKETPLACE_ICONS[network];
                      return (
                        <li key={network} style={{ '--brand': MARKETPLACE_COLORS[network] }}>
                          <motion.a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="marketplace-pill"
                            variants={{ hover: { y: -3 } }}
                            whileHover="hover"
                            whileTap={{ scale: 0.95 }}
                          >
                            {Icon && (
                              <motion.span
                                className="marketplace-icon"
                                variants={{ hover: { rotate: -8, scale: 1.12 } }}
                                transition={{ type: 'spring', stiffness: 400, damping: 12 }}
                              >
                                <Icon size={18} weight="fill" />
                              </motion.span>
                            )}
                            {label}
                          </motion.a>
                        </li>
                      );
                    })}
                  </ul>
                </nav>
              )}

              <div className="footer-col footer-col-contact">
                <h2>Contact</h2>
                <ul className="footer-contact">
                  <li>
                    <PhoneIcon size={18} weight="duotone" />
                    <a href={`tel:${phone.replace(/[^\d+]/g, '')}`}>{phone}</a>
                  </li>
                  <li>
                    <EnvelopeSimpleIcon size={18} weight="duotone" />
                    <a href={`mailto:${email}`}>{email}</a>
                  </li>
                  <li>
                    <MapPinIcon size={18} weight="duotone" />
                    <span>
                      {FOOTER.address}
                      {FOOTER.hours && <span className="footer-hours">{FOOTER.hours}</span>}
                    </span>
                  </li>
                </ul>
              </div>
            </div>

            <div className="footer-legal">
              <span>© {new Date().getFullYear()} Heaven &amp; Angel Scents</span>
            </div>
          </div>
        </motion.div>
      </footer>
    </div>
  );
}

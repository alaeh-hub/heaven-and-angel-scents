import { forwardRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { motion } from 'motion/react';
import { ArrowRightIcon, PackageIcon, SparkleIcon } from '@phosphor-icons/react';
import { ARRIVE, EASE_OUT, SPRING } from '../motion.js';
import { packagePath, peso, percent, plural } from '../utils.js';
import { AnimatedRays } from '@/components/ui/animated-rays';
import AnimatedNumber from './AnimatedNumber.jsx';
import ProductVisual from './ProductVisual.jsx';
import { scopeLabel } from './ScopeIcon.jsx';

/**
 * One package card; the whole card is the link. A plain click passes the
 * list as `background` and the card's box as `origin` so the sheet grows
 * out of it (App.jsx, DetailDialog); the href keeps new-tab/share working.
 * On first view the list price strikes through and the partner price counts
 * down. Cards never animate size/position (see PackagesSection). `large` =
 * featured tile (3+ packages), `featured` = best-value tag.
 */
const PackageCard = forwardRef(function PackageCard({ slug, pkg, index, featured, large }, ref) {
  const location = useLocation();
  const navigate = useNavigate();
  const profit = pkg.reference_total - pkg.discounted_total;
  const discounted = profit > 0.005;

  // Plain click opens the sheet; modified clicks fall through to the href.
  const open = (e) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const card = e.currentTarget.closest('.pkg-card');
    const box = card?.getBoundingClientRect();
    const origin = box && { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    navigate(packagePath(slug, pkg.package_id), { state: { background: location, origin } });
  };

  return (
    <motion.article
      ref={ref}
      className={`pkg-card glow-card${large ? ' pkg-card-featured' : ''}`}
      initial={{ opacity: 0, y: 32 }}
      whileInView={{ opacity: 1, y: 0, transition: { ...ARRIVE, delay: index * 0.06 } }}
      viewport={{ once: true, amount: 0.2 }}
      whileHover={{ y: -6, transition: SPRING }}
      transition={SPRING}
    >
      {large && <AnimatedRays className="rays" />}
      <div className="pkg-card-top">
        <div className="pkg-tag">
          <span className="pkg-tag-no" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          <span className="pkg-tag-label">
            {featured && <SparkleIcon size={12} weight="fill" />}
            {featured ? 'Best value' : scopeLabel(pkg.partner_scope)}
          </span>
        </div>
        {pkg.previews?.length > 0 && (
          <span className="pkg-bottles" aria-hidden="true">
            {pkg.previews.map((item, i) => (
              <span className="pkg-bottle" key={`${item.item_name}-${i}`} style={{ zIndex: pkg.previews.length - i }}>
                <ProductVisual item={item} alt="" />
              </span>
            ))}
          </span>
        )}
      </div>

      <h3>{pkg.package_name}</h3>
      <p className="pkg-desc">{pkg.description || 'A signature set of our fragrances.'}</p>
      <div className="pkg-meta">
        <PackageIcon size={16} />
        {plural(pkg.item_count, 'product')}
        {pkg.unit_count > 0 && <>, {plural(pkg.unit_count, 'piece')}</>}
      </div>

      <div className="pkg-price">
        <AnimatedNumber
          className="pkg-price-now"
          value={pkg.discounted_total}
          from={discounted ? pkg.reference_total : undefined}
          whenInView
          duration={1.2}
        />
        <span className="pkg-price-meta">
        {discounted && (
          <span className="pkg-price-was">
            {peso(pkg.reference_total)}
            <motion.span
              className="pkg-price-strike"
              aria-hidden="true"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, amount: 0.6 }}
              transition={{ duration: 0.45, ease: EASE_OUT, delay: 0.15 }}
            />
          </span>
        )}
        {pkg.discount_percent > 0 && (
          <span className="pkg-save"><em>{percent(pkg.discount_percent)}%</em> off</span>
        )}
        </span>
      </div>
      {(pkg.unit_count > 0 || discounted) && (
        <p className="pkg-earn">
          {pkg.unit_count > 0 && <span>{peso(pkg.discounted_total / pkg.unit_count)} per piece</span>}
          {discounted && <span>About <strong>{peso(profit)}</strong> profit at our list price</span>}
        </p>
      )}

      <Link
        to={packagePath(slug, pkg.package_id)}
        state={{ background: location }}
        onClick={open}
        className="btn btn-secondary pkg-cta"
      >
        View package <ArrowRightIcon size={16} weight="bold" />
      </Link>
    </motion.article>
  );
});

export default PackageCard;

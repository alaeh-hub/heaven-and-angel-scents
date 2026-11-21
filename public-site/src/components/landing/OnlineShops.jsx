import { ArrowUpRightIcon, ShoppingBagIcon, ShoppingCartIcon, StorefrontIcon } from '@phosphor-icons/react';
import { FOOTER } from '../../content/footer.js';
import Chapter from '../Chapter.jsx';
import Reveal from '../Reveal.jsx';

// No official marketplace marks exist, so generic icons get brand tints.
const SHOP_ICONS = {
  shopee: ShoppingBagIcon,
  lazada: ShoppingCartIcon,
  tiktokshop: StorefrontIcon,
};

const SHOP_COLORS = {
  shopee: '#EE4D2D',
  lazada: '#0F146D',
  tiktokshop: '#FE2C55',
};

const SHOP_BLURBS = {
  shopee: 'Browse the full catalog and check out with Shopee buyer protection.',
  lazada: 'Shop the collection with Lazada vouchers and cash on delivery.',
  tiktokshop: 'Order straight from our TikTok videos and lives.',
};

/**
 * Official marketplace storefronts for visitors who want to order now.
 * Reuses FOOTER.marketplaces so the footer and this section stay in sync.
 */
export default function OnlineShops() {
  const shops = FOOTER.marketplaces || [];
  if (!shops.length) return null;

  return (
    <section className="section shop-online" id="shop-online">
      <div className="container">
        <Chapter label="Shop online" title="Already know what you want?" lede="Skip the inquiry and order straight from our official stores." />

        <div className="shop-grid">
          {shops.map(({ network, label, href }, i) => {
            const Icon = SHOP_ICONS[network];
            return (
              <Reveal
                as="a"
                key={network}
                className="shop-card"
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                delay={i * 0.08}
                style={{ '--brand': SHOP_COLORS[network] }}
                whileHover={{ y: -6 }}
                whileTap={{ scale: 0.98 }}
              >
                <span className="shop-card-icon">
                  {Icon && <Icon size={26} weight="fill" />}
                </span>
                <span className="shop-card-body">
                  <span className="shop-card-name">{label}</span>
                  <span className="shop-card-blurb">{SHOP_BLURBS[network]}</span>
                </span>
                <span className="shop-card-arrow">
                  <ArrowUpRightIcon size={18} weight="bold" />
                </span>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}

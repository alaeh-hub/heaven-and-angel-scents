import { forwardRef } from 'react';
import { motion } from 'motion/react';

import { SCENTS } from '../content/scents.js';

// Photo wins; else the scent render (by name, then SKU code like "A1-85ML");
// else the first real bottle of its gender (never a stock image).
const byName = new Map(SCENTS.map((s) => [s.name.toLowerCase(), s]));
const byCode = new Map(SCENTS.map((s) => [s.code.toUpperCase(), s]));
const defaultFor = (variant) => SCENTS.find((s) => s.variant === (variant === 'Male' ? 'Male' : 'Female'));

export function productImage(item) {
  if (item.image_url) return item.image_url;
  const code = String(item.sku || '').split('-')[0].toUpperCase();
  const scent = byName.get(String(item.item_name || '').trim().toLowerCase())
    || byCode.get(code)
    || defaultFor(item.variant);
  return scent.bottle;
}

// Scent renders are wide layers with the bottle on the right, so `.is-render`
// (detail.css) centres them; uploaded photos and -thumb.webp need nothing.
const isRender = (item) => {
  const src = productImage(item);
  return src.includes('/static/img/scents/') && !src.includes('-thumb.');
};

/** A product photo or its gender placeholder; accepts motion props. */
const ProductVisual = forwardRef(function ProductVisual({ item, className = '', ...motionProps }, ref) {
  return (
    <motion.img
      ref={ref}
      src={productImage(item)}
      alt={item.item_name}
      className={`product-visual${isRender(item) ? ' is-render' : ''} ${className}`}
      loading="lazy"
      draggable={false}
      {...motionProps}
    />
  );
});

export default ProductVisual;

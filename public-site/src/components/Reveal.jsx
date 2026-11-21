import { motion } from 'motion/react';
import { ARRIVE } from '../motion.js';

/** Fades and lifts content in on first view (reduced motion keeps only the fade). */
export default function Reveal({ as = 'div', delay = 0, y = 28, amount = 0.3, children, ...rest }) {
  const Tag = motion[as];
  return (
    <Tag
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount }}
      transition={{ ...ARRIVE, delay }}
      {...rest}
    >
      {children}
    </Tag>
  );
}

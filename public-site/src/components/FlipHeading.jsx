import { useRef } from 'react';
import { useInView } from 'motion/react';
import { FlipText } from '@/components/ui/flip-text';

/** Heading whose letters flip once on first view (see skin.css); aria-label holds the text. */
export default function FlipHeading({ as: Tag = 'h2', text, className = '', duration = 1.5 }) {
  const ref = useRef(null);
  const seen = useInView(ref, { once: true, amount: 0.6 });
  return (
    <Tag ref={ref} className={className} aria-label={text}>
      <span aria-hidden="true" className={seen ? 'flip-in-view' : ''}>
        <FlipText duration={duration} loop={false}>{text}</FlipText>
      </span>
    </Tag>
  );
}

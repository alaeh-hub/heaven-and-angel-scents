import Reveal from './Reveal.jsx';
import FlipHeading from './FlipHeading.jsx';

/** Landing section opener: numbered rule, flipping headline, lede (`side` = right column). */
export default function Chapter({ n, label, title, lede, children, className = '' }) {
  return (
    <header className={`chapter ${className}`}>
      <Reveal className="chapter-meta" y={12}>
        <span className="chapter-n" aria-hidden={n ? undefined : 'true'}>{n ? String(n).padStart(2, '0') : '✦'}</span>
        <span className="chapter-rule" aria-hidden="true" />
        <span className="chapter-label">{label}</span>
      </Reveal>
      <div className="chapter-body">
        <FlipHeading className="chapter-title" text={title} />
        {(lede || children) && (
          <Reveal className="chapter-side" delay={0.15}>
            {lede && <p className="lede">{lede}</p>}
            {children}
          </Reveal>
        )}
      </div>
    </header>
  );
}

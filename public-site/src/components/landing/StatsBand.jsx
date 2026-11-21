import { FACTS, TESTIMONIALS } from '../../content/partner.js';
import { percent } from '../../utils.js';
import AnimatedNumber from '../AnimatedNumber.jsx';
import { useSite } from '../InquiryProvider.jsx';
import Reveal from '../Reveal.jsx';

const count = (v) => String(Math.round(v));

/**
 * Facts buyers weigh under the packages: real counts, best discount, reply
 * time, plus content/partner.js entries. Unknown figures are left out.
 */
export default function StatsBand({ topDiscount }) {
  const { site } = useSite();
  const stats = site?.stats;

  const items = [];
  if (stats?.scents) items.push({ key: 'scents', value: <AnimatedNumber value={stats.scents} from={0} format={count} whenInView />, label: 'scents in the catalog' });
  if (stats?.packages) items.push({ key: 'packages', value: <AnimatedNumber value={stats.packages} from={0} format={count} whenInView />, label: stats.packages === 1 ? 'package ready to order' : 'packages ready to order' });
  if (topDiscount > 0) items.push({ key: 'discount', value: <>Up to <AnimatedNumber value={topDiscount} from={0} format={(v) => `${percent(Math.round(v * 10) / 10)}%`} whenInView /></>, label: 'below our list price' });
  // No stand-in when no reply time is set ("No login needed" is in How it works).
  if (site?.reply_time) items.push({ key: 'reply', value: 'We reply', label: site.reply_time });
  FACTS.forEach((fact) => items.push({ key: fact.label, value: fact.value, label: fact.label }));

  return (
    <div className="stats">
      <Reveal as="dl" className="stats-band" amount={0.4}>
        {items.map((item) => (
          <div className="stats-item" key={item.key}>
            <dt className="sr-only">{item.label}</dt>
            <dd>
              <span className="stats-value">{item.value}</span>
              <span className="stats-label" aria-hidden="true">{item.label}</span>
            </dd>
          </div>
        ))}
      </Reveal>

      {TESTIMONIALS.length > 0 && (
        <div className="quotes">
          {TESTIMONIALS.map((t, i) => (
            <Reveal as="figure" className="quote" key={t.name} delay={i * 0.08}>
              <blockquote>&ldquo;{t.quote}&rdquo;</blockquote>
              <figcaption>
                <strong>{t.name}</strong>
                <span>{[t.role, t.place].filter(Boolean).join(', ')}</span>
              </figcaption>
            </Reveal>
          ))}
        </div>
      )}
    </div>
  );
}

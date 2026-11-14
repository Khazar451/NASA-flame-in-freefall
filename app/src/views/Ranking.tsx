import { useMemo, useState } from 'react';
import InvestigationDetail, { FUEL_LABEL, ROLE_LABEL } from '../components/InvestigationDetail';
import { name } from '../data';
import { DEFAULT_WEIGHTS, HABITATS, PARTS, toPsi, type PartKey } from '../habitats';
import { rank, type Scored } from '../scoring';
import { isCoolFlameStudy } from '../topics';
import type { Dataset } from '../types';

// Fixed order, never reassigned when a weight goes to zero.
const PART_COLORS: Record<PartKey, string> = {
  atmosphere: '#2a78d6',
  fuel: '#eb6834',
  role: '#1baf7a',
  evidence: '#eda100',
};
const ROLE_WORD = { prevention: 'prevention', detection: 'detection', suppression: 'suppression', fundamentals: 'flame-science' } as const;
const fmt = (n: number) => n.toFixed(1).replace(/\.0$/, '');

function why(s: Scored): string {
  const lead = s.inv.project_type === 'ground'
    ? 'Ground re-analysis of flight data.'
    : `${s.inv.fuel_phase === 'other' ? 'Aerosol' : s.inv.fuel_phase[0].toUpperCase() + s.inv.fuel_phase.slice(1) + '-fuel'} ${ROLE_WORD[s.inv.role]} study.`;
  if (s.total === 0) return `${lead} No per-test data on file, so it scores on fuel type and role only.`;
  if (s.tests === 0) {
    return `${lead} ${s.total} ${s.total === 1 ? 'test' : 'tests'} on file, but the source table does not give both oxygen and pressure, so nothing can be matched to this atmosphere.`;
  }
  if (s.inside > 0) return `${lead} ${s.inside} of ${s.tests} tests ${s.inside === 1 ? 'falls' : 'fall'} inside this atmosphere.`;
  const c = s.closest;
  return c && c.o2_pct != null && c.pressure_kpa != null
    ? `${lead} Closest test: ${fmt(c.o2_pct)}% oxygen at ${fmt(toPsi(c.pressure_kpa))} psi.`
    : lead;
}

// One line of facts for the summary shown when a name is hovered.
function facts(s: Scored): string {
  const tests =
    s.total === 0
      ? 'no per-test data'
      : `${s.total.toLocaleString('en-US')} ${s.total === 1 ? 'test' : 'tests'} on file`;
  return [
    s.inv.project_type === 'flight' ? 'Flight experiment' : 'Ground study',
    ROLE_LABEL[s.inv.role],
    `${FUEL_LABEL[s.inv.fuel_phase]} fuel`,
    tests,
  ].join(' · ');
}

export default function Ranking({ data }: { data: Dataset }) {
  const [habitatId, setHabitatId] = useState('lunar');
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS);
  const [open, setOpen] = useState<string | null>(null); // investigation whose full record is shown
  const byId = useMemo(() => new Map(data.investigations.map((i) => [i.id, i])), [data]);
  const habitat = HABITATS.find((h) => h.id === habitatId)!;
  const ranked = useMemo(() => rank(data.investigations, data.tests, habitat, weights), [data, habitat, weights]);
  const total = PARTS.reduce((s, p) => s + weights[p.key], 0);
  const [coolOnly, setCoolOnly] = useState(false);
  // Each row keeps its place in the full ranking, so a filtered list still shows true ranks.
  const rows = ranked.map((s, i) => ({ s, place: i + 1 })).filter(({ s }) => !coolOnly || isCoolFlameStudy(s.inv));

  return (
    <section className="split">
      <div className="panel">
        <h2>Which investigations matter most for this habitat</h2>
        <p className="lede">
          The score is a weighted sum of four parts. Nothing here is a model's opinion: change a weight and the
          order is recalculated in front of you.
        </p>
        <div className="chips" role="group" aria-label="Habitat">
          {HABITATS.map((h) => (
            <button key={h.id} type="button" aria-pressed={h.id === habitatId} onClick={() => setHabitatId(h.id)}>
              {h.label}
            </button>
          ))}
        </div>
        <p className="quiet">{habitat.basis}</p>
        <div className="chips" role="group" aria-label="Investigations listed">
          <button type="button" aria-pressed={!coolOnly} onClick={() => setCoolOnly(false)}>
            All investigations
          </button>
          <button type="button" aria-pressed={coolOnly} onClick={() => setCoolOnly(true)}>
            Cool flames
          </button>
        </div>

        <ul className="legend" aria-label="Score parts">
          {PARTS.map((p) => (
            <li key={p.key}>
              <i style={{ background: PART_COLORS[p.key] }} />
              {p.label}
            </li>
          ))}
        </ul>

        <table className="rank">
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Investigation</th>
              <th scope="col">Score</th>
              <th scope="col">What the score is made of</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ s, place }) => [
              <tr key={s.inv.id} className={open === s.inv.id ? 'opened' : undefined}>
                <td className="num">{place}</td>
                <td className="who">
                  <button
                    type="button"
                    className="who-name"
                    aria-expanded={open === s.inv.id}
                    aria-describedby={open === s.inv.id ? undefined : `summary-${s.inv.id}`}
                    onClick={() => setOpen(open === s.inv.id ? null : s.inv.id)}
                  >
                    {name(s.inv)}
                  </button>
                  <span className="sub">{s.inv.title}</span>
                  {open !== s.inv.id && (
                    <div className="card" role="tooltip" id={`summary-${s.inv.id}`}>
                      <p className="card-title">{s.inv.title}</p>
                      <p className="card-facts">{facts(s)}</p>
                      {s.inv.objectives && <p className="clamp">{s.inv.objectives}</p>}
                      <p className="card-hint">Select the name for the full record.</p>
                    </div>
                  )}
                </td>
                <td className="num strong">{Math.round(s.score)}</td>
                <td>
                  <div className="bar" aria-hidden="true">
                    {total > 0 && PARTS.map((p) => {
                      const share = (100 * weights[p.key] * s.parts[p.key]) / total;
                      return share > 0.5 ? (
                        <span
                          key={p.key}
                          style={{ width: `${share}%`, background: PART_COLORS[p.key] }}
                          title={`${p.label}: ${Math.round(share)} of ${Math.round((100 * weights[p.key]) / total)} points`}
                        />
                      ) : null;
                    })}
                  </div>
                  <span className="sub">{why(s)}</span>
                </td>
              </tr>,
              open === s.inv.id && (
                <tr key={`${s.inv.id}-detail`} className="detail-row">
                  <td />
                  <td colSpan={3}>
                    <InvestigationDetail
                      inv={s.inv}
                      tests={data.tests.filter((t) => t.investigation_id === s.inv.id)}
                      byId={byId}
                    />
                  </td>
                </tr>
              ),
            ])}
          </tbody>
        </table>
      </div>

      <aside className="stack">
        <div className="panel">
          <h3>Weights</h3>
          {PARTS.map((p) => (
            <label key={p.key} className="slider">
              <span>
                <i style={{ background: PART_COLORS[p.key] }} />
                {p.label}
                <b>{total > 0 ? Math.round((100 * weights[p.key]) / total) : 0}%</b>
              </span>
              <input
                type="range" min={0} max={100} step={5} value={weights[p.key]}
                onChange={(e) => setWeights({ ...weights, [p.key]: Number(e.target.value) })}
              />
              <small>{p.help}</small>
            </label>
          ))}
          <button type="button" className="plain" onClick={() => setWeights(DEFAULT_WEIGHTS)}>
            Reset weights
          </button>
        </div>
        {!data.rolesReviewed && (
          <div className="panel">
            <h3>Draft settings</h3>
            <p className="quiet">
              The role tags and the fuel and role values are a first draft. Edit them in <code>src/habitats.ts</code>{' '}
              and in the investigations file once the domain lead has reviewed them.
            </p>
          </div>
        )}
      </aside>
    </section>
  );
}

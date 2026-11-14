import { useState } from 'react';
import { name } from '../data';
import { formula, numberedList } from '../format';
import { HABITATS, toPsi } from '../habitats';
import { hasAtmosphere, insideHabitat } from '../scoring';
import type { FuelPhase, Investigation, Role, TestRow } from '../types';

export const ROLE_LABEL: Record<Role, string> = {
  prevention: 'Prevention',
  detection: 'Detection',
  suppression: 'Suppression',
  fundamentals: 'Flame science',
};
export const FUEL_LABEL: Record<FuelPhase, string> = { solid: 'Solid', liquid: 'Liquid', gas: 'Gas', other: 'Other' };
const LUNAR = HABITATS.find((h) => h.id === 'lunar')!;
const LISTED = 4; // fuels named before the rest fold into "and n more"

const fmt = (n: number) => n.toFixed(1).replace(/\.0$/, '');
const range = (values: number[], unit: string) => {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return fmt(lo) === fmt(hi) ? `${fmt(lo)}${unit}` : `${fmt(lo)} to ${fmt(hi)}${unit}`;
};

// Distinct values with how often each occurs, most frequent first.
function tally(values: (string | null)[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  return [...counts].sort((a, b) => b[1] - a[1]);
}

// Why a test cannot be placed on the map, which needs both oxygen and pressure.
function whyOffMap(t: TestRow): string {
  if (t.o2_pct == null && t.pressure_kpa == null) return 'the source table gives neither oxygen nor pressure';
  if (t.o2_pct != null) return 'the source table gives no usable pressure';
  return /inverse flame/i.test(t.note ?? '')
    ? 'inverse flames, which have no surrounding oxygen level'
    : 'the source table gives no oxygen level';
}

// Long record text, cut short until asked for. Records that number their
// points inline ("1) … 2) …") are shown as a list, one point per line.
function Clamped({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const list = numberedList(text);
  return (
    <>
      {!list && <p className={open ? 'detail-text' : 'detail-text clamp'}>{text}</p>}
      {list && (
        <div className={open ? 'detail-text' : 'detail-text cut'}>
          {list.intro && <p>{list.intro}</p>}
          <ol>
            {list.items.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ol>
        </div>
      )}
      <button type="button" className="more" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Show less' : 'Show more'}
      </button>
    </>
  );
}

interface Props {
  inv: Investigation;
  tests: TestRow[]; // every test row of this investigation
  byId: Map<string, Investigation>;
}

export default function InvestigationDetail({ inv, tests, byId }: Props) {
  const placed = tests.filter(hasAtmosphere);
  const offMap = tally(tests.filter((t) => !hasAtmosphere(t)).map(whyOffMap));
  const o2 = tests.map((t) => t.o2_pct).filter((v): v is number => v != null);
  const psi = tests.map((t) => t.pressure_kpa).filter((v): v is number => v != null).map(toPsi);
  const flow = tests.map((t) => t.flow_cm_s).filter((v): v is number => v != null);
  const fuels = tally(tests.map((t) => t.fuel_material));
  // The source writes one mixture two ways (N2+CO2, CO2+N2); sort the parts so they count as one.
  const gases = tally(tests.map((t) => t.diluent && t.diluent.split('+').map((g) => g.trim()).sort().join(' + ')));
  const assumed = placed.filter((t) => t.note && /assum/i.test(t.note)).length;
  const inLunar = placed.filter((t) => insideHabitat(t, LUNAR)).length;

  const outcomes = [
    { key: 'burned', label: 'Kept burning', n: tests.filter((t) => t.outcome === 'sustained').length },
    {
      key: 'out',
      label: 'Went out or did not light',
      n: tests.filter((t) => t.outcome === 'extinguished' || t.outcome === 'no_ignition').length,
    },
    { key: 'unknown', label: 'Not recorded', n: tests.filter((t) => t.outcome === 'unknown').length },
  ];

  return (
    <>
      <p className="name">{name(inv)}</p>
      <p>{inv.title}</p>

      <dl>
        <dt>Type</dt>
        <dd>{inv.project_type === 'flight' ? 'Flight experiment' : 'Ground study'}</dd>
        <dt>Role</dt>
        <dd>{ROLE_LABEL[inv.role]}</dd>
        {inv.sub_area && (
          <>
            <dt>Research area</dt>
            <dd>{inv.sub_area}</dd>
          </>
        )}
        <dt>Fuel type</dt>
        <dd>{FUEL_LABEL[inv.fuel_phase]}</dd>
        {inv.hardware && (
          <>
            <dt>Hardware</dt>
            <dd>{inv.hardware}</dd>
          </>
        )}
        {inv.n_publications != null && (
          <>
            <dt>Publications</dt>
            <dd>{inv.n_publications}</dd>
          </>
        )}
        {inv.related.length > 0 && (
          <>
            <dt>Related</dt>
            <dd>{inv.related.map((id) => { const r = byId.get(id); return r ? name(r) : id; }).join(', ')}</dd>
          </>
        )}
      </dl>

      <h4>Tests on file</h4>
      {tests.length === 0 && <p className="quiet">No per-test data on file for this investigation.</p>}
      {tests.length > 0 && (
        <>
          <dl>
            <dt>Tests</dt>
            <dd>
              {tests.length.toLocaleString('en-US')}
              {placed.length < tests.length && `, ${placed.length.toLocaleString('en-US')} of them on the map`}
              {offMap.map(([reason, n]) => (
                <span key={reason} className="why">
                  {n.toLocaleString('en-US')} not drawn: {reason}.
                </span>
              ))}
            </dd>
            {o2.length > 0 && (
              <>
                <dt>Oxygen</dt>
                <dd>{range(o2, '%')}</dd>
              </>
            )}
            {psi.length > 0 && (
              <>
                <dt>Pressure</dt>
                <dd>
                  {range(psi, ' psi')}
                  {assumed > 0 && ` (assumed for ${assumed === placed.length ? 'all' : assumed} tests)`}
                </dd>
              </>
            )}
            {flow.length > 0 && (
              <>
                <dt>Air flow</dt>
                <dd>{range(flow, ' cm/s')}</dd>
              </>
            )}
            {fuels.length > 0 && (
              <>
                <dt>Fuels</dt>
                <dd>
                  {fuels.slice(0, LISTED).map(([f, n]) => `${formula(f)} (${n})`).join(', ')}
                  {fuels.length > LISTED && `, and ${fuels.length - LISTED} more`}
                </dd>
              </>
            )}
            {gases.length > 0 && (
              <>
                <dt>Mixed with</dt>
                <dd>{gases.map(([g]) => formula(g)).join(', ')}</dd>
              </>
            )}
            {placed.length > 0 && (
              <>
                <dt>In lunar range</dt>
                <dd>{inLunar === 0 ? 'None' : `${inLunar} of ${placed.length} on the map`}</dd>
              </>
            )}
          </dl>

          <p className="detail-label">How the flames ended</p>
          <div className="outcomes" aria-hidden="true">
            {outcomes.filter((o) => o.n > 0).map((o) => (
              <span key={o.key} className={o.key} style={{ flexGrow: o.n }} />
            ))}
          </div>
          <ul className="outcome-key">
            {outcomes.map((o) => (
              <li key={o.key}>
                <span className={`swatch ${o.key}`} />
                {o.label}
                <b>{o.n.toLocaleString('en-US')}</b>
              </li>
            ))}
          </ul>
        </>
      )}

      {inv.objectives && (
        <>
          <h4>Objectives</h4>
          <Clamped text={inv.objectives} />
        </>
      )}
      {inv.earth_benefits && (
        <>
          <h4>Benefits on Earth</h4>
          <Clamped text={inv.earth_benefits} />
        </>
      )}

      <p className="links">
        {inv.doi && (
          <a href={`https://doi.org/${inv.doi}`} target="_blank" rel="noreferrer">
            DOI {inv.doi}
          </a>
        )}
        <a href={inv.url} target="_blank" rel="noreferrer">
          {inv.id} in the PSI repository
        </a>
      </p>
    </>
  );
}

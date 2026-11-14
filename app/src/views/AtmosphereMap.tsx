import { useMemo, useState } from 'react';
import Plot from '../components/Plot';
import { name } from '../data';
import { HABITATS, toPsi } from '../habitats';
import { hasAtmosphere, insideHabitat } from '../scoring';
import type { Dataset, FuelPhase, Role, TestRow } from '../types';

// Fuel phase carries colour and shape together, so the chart still reads
// without colour. Three series is the validated limit for a scatter.
const PHASES: { key: FuelPhase; label: string; color: string; symbol: string }[] = [
  { key: 'solid', label: 'Solid fuel', color: '#2a78d6', symbol: 'circle' },
  { key: 'liquid', label: 'Liquid droplets', color: '#eb6834', symbol: 'square' },
  { key: 'gas', label: 'Gas flames', color: '#1baf7a', symbol: 'triangle-up' },
];
const ROLES: { key: Role | 'all'; label: string }[] = [
  { key: 'all', label: 'All studies' },
  { key: 'prevention', label: 'Prevention' },
  { key: 'detection', label: 'Detection' },
  { key: 'suppression', label: 'Suppression' },
  { key: 'fundamentals', label: 'Flame science' },
];
const INK = '#16202e';
const SURFACE = '#fcfcfb';
const LUNAR = HABITATS.find((h) => h.id === 'lunar')!;
const POINTS = HABITATS.filter((h) => h.id !== 'lunar');
// Drawn in this order, so solid fuel (what a cabin is made of) ends on top.
const DRAW_ORDER: FuelPhase[] = ['gas', 'liquid', 'solid'];
const went_out = (t: TestRow) => t.outcome === 'extinguished' || t.outcome === 'no_ignition';
const count = (n: number, word: string) => `${n.toLocaleString('en-US')} ${word}${n === 1 ? '' : 's'}`;
const fmt = (n: number, d = 1) => n.toFixed(d).replace(/\.0$/, '');
const span = (values: number[], d = 1) => {
  const lo = fmt(Math.min(...values), d);
  const hi = fmt(Math.max(...values), d);
  return lo === hi ? lo : `${lo} to ${hi}`;
};
const mean = (values: number[]) => values.reduce((s, v) => s + v, 0) / values.length;

type Placed = TestRow & { o2_pct: number; pressure_kpa: number };
interface Group {
  inv: string;
  tests: Placed[];
  psi: number;
  o2: number;
}

// Hundreds of tests share a handful of conditions, so drawing one mark per
// test hides most of them. Instead: one mark per investigation and fuel at
// each tested condition (oxygen to the nearest 1 %, pressure to the nearest
// 0.5 psi), placed at the group's average and sized by how many tests it holds.
function groupTests(rows: Placed[]): Group[] {
  const groups = new Map<string, Placed[]>();
  for (const t of rows) {
    const key = [t.investigation_id, t.fuel_phase, Math.round(t.o2_pct), Math.round(toPsi(t.pressure_kpa) * 2)].join('|');
    const list = groups.get(key);
    if (list) list.push(t);
    else groups.set(key, [t]);
  }
  return [...groups.values()]
    .map((tests) => ({
      inv: tests[0].investigation_id,
      tests,
      psi: mean(tests.map((t) => toPsi(t.pressure_kpa))),
      o2: mean(tests.map((t) => t.o2_pct)),
    }))
    .sort((a, b) => b.tests.length - a.tests.length); // big first, so small marks end on top
}
// Mark diameter in pixels: area grows with the number of tests.
const diameter = (n: number) => Math.min(34, 8 + 3 * Math.sqrt(n));
const LABEL_FROM = 10; // marks this big carry their count

function describe(g: Group, title: string): string {
  const n = g.tests.length;
  const kpa = g.tests.map((t) => t.pressure_kpa);
  const lines = [
    `<b>${title}</b>: ${count(n, 'test')}`,
    `${span(g.tests.map((t) => t.o2_pct))}% oxygen at ${span(kpa.map(toPsi))} psi (${span(kpa, 0)} kPa)`,
  ];
  const fuels = [...new Set(g.tests.map((t) => t.fuel_material).filter((f): f is string => !!f))];
  if (fuels.length > 0) {
    lines.push(`Fuel: ${fuels.slice(0, 3).join('; ')}${fuels.length > 3 ? `; ${fuels.length - 3} more` : ''}`);
  }
  const flows = g.tests.map((t) => t.flow_cm_s).filter((f): f is number => f != null);
  if (flows.length > 0) lines.push(`Air flow ${span(flows)} cm/s`);
  const burned = g.tests.filter((t) => t.outcome === 'sustained').length;
  const out = g.tests.filter(went_out).length;
  const unknown = n - burned - out;
  const ends = [
    burned > 0 && `${burned} kept burning`,
    out > 0 && `${out} went out or did not light`,
    unknown > 0 && (unknown === n ? 'Outcome not recorded in the source table' : `${unknown} not recorded`),
  ].filter(Boolean);
  lines.push(ends.join(', '));
  const assumed = g.tests.filter((t) => t.note?.toLowerCase().includes('assumed')).length;
  if (assumed > 0) lines.push(`<i>Pressure assumed, not measured${assumed < n ? `, for ${assumed} of them` : ''}</i>`);
  const sample = g.tests.filter((t) => t.sample).length;
  if (sample > 0) lines.push(`<i>${count(sample, 'placeholder row')}, not NASA results</i>`);
  return lines.join('<br>');
}

export default function AtmosphereMap({ data }: { data: Dataset }) {
  const [role, setRole] = useState<Role | 'all'>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState<'full' | 'tested'>('full');
  const byId = useMemo(() => new Map(data.investigations.map((i) => [i.id, i])), [data]);

  const shown = useMemo(
    () => data.tests.filter(hasAtmosphere).filter((t) => role === 'all' || byId.get(t.investigation_id)?.role === role),
    [data, role, byId],
  );

  // Oxygen axis: the whole 0-100 % scale, or only the band the tests cover.
  const yRange = useMemo<[number, number]>(() => {
    if (zoom === 'full' || shown.length === 0) return [4, 107];
    const o2 = shown.map((t) => t.o2_pct);
    return [Math.max(0, Math.floor(Math.min(...o2) / 5) * 5 - 2), Math.ceil(Math.max(...o2) / 5) * 5 + 3];
  }, [zoom, shown]);

  const traces = useMemo(() => {
    const [p0, p1] = LUNAR.psi;
    const [o0, o1] = LUNAR.o2;
    // Keep the zone's label on the chart when its top edge is zoomed away.
    const zoneLabelY = o1 - 4 < yRange[1] ? o1 - 4 : o0 + 1.5;
    const zone = {
      type: 'scatter', mode: 'lines', x: [p0, p1, p1, p0, p0], y: [o0, o0, o1, o1, o0],
      fill: 'toself', fillcolor: 'rgba(22,32,46,0.07)', line: { color: INK, width: 1, dash: 'dot' },
      hoverinfo: 'skip', showlegend: false,
    };
    const zoneLabel = {
      type: 'scatter', mode: 'text', x: [Math.sqrt(p0 * p1)], y: [zoneLabelY], text: ['Lunar base / rover range'],
      textfont: { color: INK, size: 12 }, hoverinfo: 'skip', showlegend: false,
    };
    const refs = {
      type: 'scatter', mode: 'markers+text', showlegend: false,
      x: POINTS.map((h) => h.psi[0]), y: POINTS.map((h) => h.o2[0]),
      text: POINTS.map((h) => h.short), textposition: 'top right',
      textfont: { color: INK, size: 12 },
      marker: { symbol: 'diamond-open', size: 13, color: INK, line: { width: 2 } },
      hovertext: POINTS.map((h) => h.basis), hovertemplate: '%{hovertext}<extra></extra>',
    };
    const series = DRAW_ORDER.map((key) => {
      const ph = PHASES.find((p) => p.key === key)!;
      const groups = groupTests(shown.filter((t) => t.fuel_phase === ph.key));
      return {
        type: 'scatter', mode: 'markers+text', name: ph.label, showlegend: false,
        x: groups.map((g) => g.psi), y: groups.map((g) => g.o2),
        customdata: groups.map((g) => g.inv),
        marker: {
          color: ph.color, symbol: ph.symbol, opacity: 0.8,
          size: groups.map((g) => diameter(g.tests.length)),
          line: { width: 1.5, color: SURFACE },
        },
        text: groups.map((g) => (g.tests.length >= LABEL_FROM ? String(g.tests.length) : '')),
        textposition: 'middle center', textfont: { color: '#ffffff', size: 11 },
        hovertext: groups.map((g) => {
          const inv = byId.get(g.inv);
          return describe(g, inv ? name(inv) : g.inv);
        }),
        hovertemplate: '%{hovertext}<extra></extra>',
      };
    });
    // Legend entries drawn on their own, so each shows one plain mark at a
    // fixed size whatever the size of the first group in its series.
    const legend = PHASES.map((ph) => ({
      type: 'scatter', mode: 'markers', name: ph.label, x: [null], y: [null], hoverinfo: 'skip',
      marker: { color: ph.color, size: 9, symbol: ph.symbol },
    }));
    return [zone, zoneLabel, ...series, ...legend, refs];
  }, [shown, byId, yRange]);

  const layout = useMemo(
    () => ({
      height: 560,
      margin: { l: 64, r: 24, t: 16, b: 56 },
      paper_bgcolor: SURFACE,
      plot_bgcolor: SURFACE,
      font: { family: '"IBM Plex Sans", system-ui, sans-serif', size: 13, color: INK },
      xaxis: {
        title: { text: 'Total pressure (psi, log scale)' }, type: 'log', range: [Math.log10(3), Math.log10(90)],
        tickvals: [3, 4.3, 7, 10.2, 14.7, 30, 45, 75], gridcolor: '#e6e9ed', zeroline: false,
      },
      yaxis: { title: { text: 'Oxygen (% by volume)' }, range: yRange, gridcolor: '#e6e9ed', zeroline: false },
      legend: { orientation: 'h', y: 1.08, x: 0 },
      hoverlabel: { bgcolor: '#ffffff', bordercolor: '#c9d0d8', font: { color: INK, size: 13 }, align: 'left' },
    }),
    [yRange],
  );
  const biggest = useMemo(() => Math.max(0, ...groupTests(shown).map((g) => g.tests.length)), [shown]);

  // Tests the chart cannot place, under the same role filter.
  const inRole = data.tests.filter((t) => role === 'all' || byId.get(t.investigation_id)?.role === role);
  const offChart = inRole.length - shown.length;
  const lunarInside = shown.filter((t) => insideHabitat(t, LUNAR));
  const lunarFrom = [...new Set(lunarInside.map((t) => t.investigation_id))].map((id) => {
    const inv = byId.get(id);
    return inv ? name(inv) : id;
  });
  const lunarSolid = lunarInside.filter((t) => t.fuel_phase === 'solid').length;
  const cabin = HABITATS[0];
  const nearCabin = shown.filter((t) => Math.abs(t.o2_pct - cabin.o2[0]) <= 1.5 && Math.abs(toPsi(t.pressure_kpa) - cabin.psi[0]) <= 0.5);
  const sel = selected ? byId.get(selected) : undefined;
  const selTests = sel ? data.tests.filter((t) => t.investigation_id === sel.id) : [];

  return (
    <section className="split">
      <div className="panel">
        <h2>Where the experiments were run, and where crews will live</h2>
        <p className="lede">
          Each mark is a group of tests that one investigation ran at the same oxygen level and pressure: the bigger
          the mark, the more tests. Flammability rises quickly with oxygen, so the tests that matter most for a
          habitat are the ones run near its atmosphere.
        </p>
        <div className="toolbar">
          <div className="chips" role="group" aria-label="Filter by fire-safety role">
            {ROLES.map((r) => (
              <button key={r.key} type="button" aria-pressed={role === r.key} onClick={() => setRole(r.key)}>
                {r.label}
              </button>
            ))}
          </div>
          <div className="chips" role="group" aria-label="Oxygen range shown">
            <button type="button" aria-pressed={zoom === 'full'} onClick={() => setZoom('full')}>
              Full range
            </button>
            <button type="button" aria-pressed={zoom === 'tested'} onClick={() => setZoom('tested')}>
              Tested range
            </button>
          </div>
        </div>
        <Plot
          data={traces}
          layout={layout}
          onPointClick={(id) => typeof id === 'string' && setSelected(id)}
          label="Bubble chart of test oxygen level against total pressure, sized by number of tests, with habitat atmospheres marked"
        />
        <p className="quiet">
          {biggest >= LABEL_FROM && `A number on a mark is its test count; the largest holds ${biggest}. `}
          Hover a mark for its tests and how their flames ended. Select it to see its investigation.{' '}
          {zoom === 'full'
            ? 'Full range shows the whole oxygen scale, including the atmospheres nobody has tested.'
            : 'Tested range zooms in on the oxygen levels the tests cover; the spacesuit and most of the lunar range are above it.'}
        </p>
        {offChart > 0 && (
          <p className="quiet">
            {shown.length === 0
              ? `None of the ${count(offChart, 'test')} in this group can be drawn,`
              : `${count(offChart, 'more test')} ${offChart === 1 ? 'is' : 'are'} on file but not drawn,`}{' '}
            because the source table does not give both oxygen and pressure. Where a pressure is assumed, not measured, the hover note
            says so.
          </p>
        )}
      </div>

      <aside className="stack">
        <div className="panel">
          <h3>Coverage of the lunar range</h3>
          <p className="figure">
            {lunarInside.length} <span>of {count(shown.length, 'test')} shown</span>
          </p>
          <p>
            {lunarFrom.length > 0 && `All from ${lunarFrom.join(', ')}. `}
            {lunarSolid === 0
              ? 'No solid-fuel test sits inside the lunar base range.'
              : `${lunarSolid} of them burned solid fuel, the kind of material a cabin is made of.`}{' '}
            {count(nearCabin.length, 'test')} {nearCabin.length === 1 ? 'sits' : 'sit'} at or near ordinary cabin
            air.
          </p>
          <p className="quiet">{LUNAR.basis}</p>
        </div>

        <div className="panel">
          <h3>Investigation</h3>
          {!sel && <p className="quiet">Select a mark on the chart.</p>}
          {sel && (
            <>
              <p className="name">{name(sel)}</p>
              <p>{sel.title}</p>
              <dl>
                <dt>Role</dt>
                <dd>{ROLES.find((r) => r.key === sel.role)?.label}</dd>
                <dt>Hardware</dt>
                <dd>{sel.hardware ?? 'Ground study'}</dd>
                <dt>Tests on file</dt>
                <dd>{selTests.length}</dd>
                {sel.related.length > 0 && (
                  <>
                    <dt>Related</dt>
                    <dd>{sel.related.map((id) => { const r = byId.get(id); return r ? name(r) : id; }).join(', ')}</dd>
                  </>
                )}
              </dl>
              <a href={sel.doi ? `https://doi.org/${sel.doi}` : sel.url} target="_blank" rel="noreferrer">
                {sel.doi ? `DOI ${sel.doi}` : `Open ${sel.id} in the PSI repository`}
              </a>
            </>
          )}
        </div>
      </aside>
    </section>
  );
}

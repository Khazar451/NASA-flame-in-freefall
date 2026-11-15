import { useMemo, useState } from 'react';
import { AXIS, BASE, BASELINE, INK, INK_3, MONO, SURFACE, tint } from '../chartTheme';
import Plot from '../components/Plot';
import { name } from '../data';
import { formula } from '../format';
import { toPsi } from '../habitats';
import type { Dataset, FuelPhase, TestRow } from '../types';

// Charts of measured results, for tests the atmosphere map cannot say much
// about. Every value comes from the optional `extra` fields of a test row.

const fmt = (n: number, d = 1) => n.toFixed(d).replace(/\.0$/, '');
const count = (n: number, word: string) => `${n.toLocaleString('en-US')} ${word}${n === 1 ? '' : 's'}`;
// The value a share `p` of the way through a sorted list, between neighbours when it falls there.
const quantile = (sorted: number[], p: number) => {
  const at = (sorted.length - 1) * p;
  const lo = Math.floor(at);
  const hi = Math.ceil(at);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (at - lo);
};
// A numeric value from a test's family-specific extras, or null.
const extra = (t: TestRow, key: string): number | null => {
  const v = t.extra?.[key];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
};

// ---- Droplet size at ignition against size at extinction -------------------

// The gas the oxygen was mixed with. Nitrogen alone is the baseline, so it is
// grey; the two suppressants carry colour and their own shape.
const GASES = [
  { key: 'n2', label: 'Nitrogen only', color: '#8a94a1', symbol: 'circle' },
  { key: 'co2', label: 'Carbon dioxide added', color: '#6250d6', symbol: 'square' },
  { key: 'he', label: 'Helium added', color: '#c98500', symbol: 'diamond' },
] as const;
type GasKey = (typeof GASES)[number]['key'];
const gasOf = (t: TestRow): GasKey | null => {
  const d = t.diluent ?? '';
  if (/CO2/i.test(d)) return 'co2';
  if (/\bHe\b/.test(d)) return 'he';
  return /N2/i.test(d) ? 'n2' : null;
};
const ENDED: Record<string, string> = {
  sustained: 'burned to the end of the test',
  extinguished: 'the flame went out',
  no_ignition: 'did not light',
  unknown: 'not recorded',
};

interface Droplet {
  t: TestRow;
  start: number; // mm
  end: number; // mm
}
// The visible flame went out part-way (a size above zero is recorded), yet the
// test is logged as burned to completion: the droplet went on being consumed.
// The domain lead confirmed this reading of FLEX's "Completion" on 15 Nov 2026.
const burnedOn = (d: Droplet) => d.t.outcome === 'sustained' && d.end > 0;

// ---- Burn time by fuel ------------------------------------------------------

const PHASE_COLOR: Record<FuelPhase, string> = { solid: '#2a78d6', liquid: '#eb6834', gas: '#1baf7a', other: '#8a94a1' };
const CHART_FROM = 10; // investigations with fewer timed tests are listed in words

// Each fuel gets a lane: its box in the upper half, one dot per test below it.
const BOX_AT = 0.2;
const DOTS_AT = -0.09;
const LANE_PX = 66;
// A fixed spread for the dots, so the chart looks the same on every load.
const scatter = (k: number) => (((k * 0.61803398875) % 1) - 0.5) * 0.16;

interface FuelRow {
  fuel: string;
  phase: FuelPhase;
  tests: { t: TestRow; s: number }[]; // s = burn time in seconds, shortest first
  q1: number;
  median: number;
  q3: number;
}
interface BurnGroup {
  id: string;
  title: string;
  rows: FuelRow[];
  total: number;
  unlit: number; // burn time of 0
}

export default function Results({ data }: { data: Dataset }) {
  const byId = useMemo(() => new Map(data.investigations.map((i) => [i.id, i])), [data]);
  const title = (id: string) => {
    const inv = byId.get(id);
    return inv ? name(inv) : id;
  };

  // Droplets
  const droplets = useMemo<Droplet[]>(
    () =>
      data.tests.flatMap((t) => {
        const start = extra(t, 'droplet_diameter_mm');
        const end = extra(t, 'extinction_diameter_mm');
        return start != null && end != null ? [{ t, start, end }] : [];
      }),
    [data],
  );
  const dropletFuels = useMemo(
    () => [...new Set(droplets.map((d) => d.t.fuel_material).filter((f): f is string => !!f))].sort(),
    [droplets],
  );
  const [fuel, setFuel] = useState<string>('all');
  const shown = useMemo(() => droplets.filter((d) => fuel === 'all' || d.t.fuel_material === fuel), [droplets, fuel]);
  const dropletFrom = [...new Set(droplets.map((d) => title(d.t.investigation_id)))].join(', ');

  const dropletTraces = useMemo(() => {
    const top = Math.max(1, ...droplets.map((d) => Math.max(d.start, d.end))) * 1.05;
    const line = {
      type: 'scatter', mode: 'lines', x: [0, top], y: [0, top], hoverinfo: 'skip', showlegend: true,
      name: 'No shrinking: went out at once', line: { color: '#9aa3ad', width: 1.5, dash: 'dash' },
    };
    const series = GASES.map((g) => {
      const rows = shown.filter((d) => gasOf(d.t) === g.key);
      return {
        type: 'scatter', mode: 'markers', name: `${g.label} (${rows.length})`,
        x: rows.map((d) => d.start), y: rows.map((d) => d.end),
        marker: { color: g.color, symbol: g.symbol, size: 9, opacity: 0.8, line: { width: 1, color: SURFACE } },
        hovertext: rows.map((d) => {
          const { t, start, end } = d;
          return (
          [
            `<b>${title(t.investigation_id)} test ${t.test_label}</b>`,
            `${t.fuel_material ?? 'Fuel not recorded'}: ${fmt(start, 2)} mm at ignition, ${fmt(end, 2)} mm when the flame went out`,
            t.o2_pct != null && t.pressure_kpa != null
              ? `${fmt(t.o2_pct)}% oxygen at ${fmt(toPsi(t.pressure_kpa))} psi${t.diluent ? `, ${formula(t.diluent)}` : ''}`
              : null,
            burnedOn(d)
              ? '<i>The visible flame went out, but the droplet went on burning to the end</i>'
              : `How it ended: ${ENDED[t.outcome]}`,
          ]
            .filter(Boolean)
            .join('<br>')
          );
        }),
        hovertemplate: '%{hovertext}<extra></extra>',
      };
    });
    return [line, ...series];
  }, [droplets, shown, byId]);

  const dropletLayout = useMemo(
    () => ({
      ...BASE,
      height: 480,
      margin: { l: 64, r: 24, t: 16, b: 56 },
      xaxis: { ...AXIS, ...BASELINE, title: { text: 'Droplet size at ignition (mm)' }, rangemode: 'tozero' },
      yaxis: { ...AXIS, title: { text: 'Droplet size when the flame went out (mm)' }, rangemode: 'tozero' },
      legend: { orientation: 'h', y: 1.1, x: 0 },
    }),
    [],
  );

  // Burn time
  const burn = useMemo<BurnGroup[]>(() => {
    const groups = new Map<string, Map<string, FuelRow>>();
    for (const t of data.tests) {
      const s = extra(t, 'burn_time_s');
      if (s == null) continue;
      const fuels = groups.get(t.investigation_id) ?? new Map<string, FuelRow>();
      groups.set(t.investigation_id, fuels);
      const key = t.fuel_material ?? 'Fuel not recorded';
      const row = fuels.get(key) ?? { fuel: key, phase: t.fuel_phase, tests: [], q1: 0, median: 0, q3: 0 };
      fuels.set(key, row);
      row.tests.push({ t, s });
    }
    return [...groups].map(([id, fuels]) => {
      const rows = [...fuels.values()];
      for (const r of rows) {
        r.tests.sort((a, b) => a.s - b.s);
        const times = r.tests.map((x) => x.s);
        [r.q1, r.median, r.q3] = [0.25, 0.5, 0.75].map((p) => quantile(times, p));
      }
      rows.sort((a, b) => b.median - a.median);
      const all = rows.flatMap((r) => r.tests);
      return { id, title: title(id), rows, total: all.length, unlit: all.filter((x) => x.s === 0).length };
    }).sort((a, b) => b.total - a.total);
  }, [data, byId]);

  const burnCharts = useMemo(
    () =>
      burn.filter((g) => g.total >= CHART_FROM).map((g) => {
        const lane = (i: number) => g.rows.length - 1 - i; // first fuel on top
        const traces = g.rows.flatMap((r, i) => {
          const color = PHASE_COLOR[r.phase];
          const times = r.tests.map((x) => x.s);
          const summary = [
            `<b>${formula(r.fuel)}</b>: ${count(times.length, 'test')}`,
            `Median ${fmt(r.median)} s`,
            `Middle half ${fmt(r.q1)} to ${fmt(r.q3)} s`,
            `Shortest ${fmt(times[0])} s, longest ${fmt(times[times.length - 1])} s`,
          ].join('<br>');
          return [
            // The box is drawn from the numbers worked out above, so its label and its hover text agree with it.
            {
              type: 'box', orientation: 'h', hoverinfo: 'skip', showlegend: false,
              y: [lane(i) + BOX_AT], q1: [r.q1], median: [r.median], q3: [r.q3],
              lowerfence: [times[0]], upperfence: [times[times.length - 1]],
              width: 0.26, whiskerwidth: 0.5, line: { color, width: 1.5 }, fillcolor: tint(color, 0.16),
            },
            {
              type: 'scatter', mode: 'markers', showlegend: false,
              x: times, y: times.map((_, k) => lane(i) + DOTS_AT + scatter(k)),
              marker: { color, size: 5, opacity: 0.5, line: { width: 0.5, color: SURFACE } },
              hovertext: r.tests.map(({ t, s }) =>
                [
                  `<b>${g.title} test ${t.test_label}</b>`,
                  `${formula(r.fuel)}: ${s === 0 ? 'did not light' : `burned for ${fmt(s)} s`}`,
                  t.o2_pct != null && t.pressure_kpa != null
                    ? `${fmt(t.o2_pct)}% oxygen at ${fmt(toPsi(t.pressure_kpa))} psi`
                    : null,
                ]
                  .filter(Boolean)
                  .join('<br>'),
              ),
              hovertemplate: '%{hovertext}<extra></extra>',
            },
            // The median carries its value; hovering anywhere on the box gives the summary.
            {
              type: 'scatter', mode: 'markers+text', showlegend: false, cliponaxis: false,
              x: [r.q1, r.median, r.q3], y: [0, 0, 0].map(() => lane(i) + BOX_AT),
              marker: { color, size: 24, opacity: 0 },
              text: ['', `${fmt(r.median)} s`, ''], textposition: 'top center',
              textfont: { family: MONO, size: 11, color: INK },
              hovertext: [summary, summary, summary], hovertemplate: '%{hovertext}<extra></extra>',
            },
          ];
        });
        return {
          group: g,
          traces,
          layout: {
            ...BASE,
            height: g.rows.length * LANE_PX + 72,
            margin: { l: 150, r: 28, t: 16, b: 52 },
            showlegend: false,
            hovermode: 'closest',
            xaxis: { ...AXIS, ...BASELINE, title: { text: 'Burn time (seconds)' }, rangemode: 'tozero' },
            yaxis: {
              tickvals: g.rows.map((_, i) => lane(i)),
              ticktext: g.rows.map((r) => `${formula(r.fuel)}  <span style="color:${INK_3}">${r.tests.length}</span>`),
              range: [-0.42, g.rows.length - 0.3],
              showgrid: false, zeroline: false, fixedrange: true, automargin: true,
            },
          },
        };
      }),
    [burn],
  );
  const burnFew = burn.filter((g) => g.total < CHART_FROM);
  const burnTotal = burn.reduce((s, g) => s + g.total, 0);

  return (
    <section className="results">
      <div className="panel">
        <p className="eyebrow"><b>03</b> Measured results · droplets</p>
        <h2>How much of a droplet burns before the flame goes out</h2>
        <p className="lede">
          Each mark is one fuel droplet. The further a mark sits below the dashed line, the more of the droplet
          burned before its flame went out. A suppressant that works puts flames out early, so its marks sit
          closer to the line.
        </p>
        {droplets.length === 0 && <p className="quiet">No test on file gives both droplet sizes.</p>}
        {droplets.length > 0 && (
          <>
            <div className="chips" role="group" data-label="Fuel" aria-label="Filter by fuel">
              <button type="button" aria-pressed={fuel === 'all'} onClick={() => setFuel('all')}>
                All fuels
              </button>
              {dropletFuels.map((f) => (
                <button key={f} type="button" aria-pressed={fuel === f} onClick={() => setFuel(f)}>
                  {f}
                </button>
              ))}
            </div>
            <Plot
              data={dropletTraces}
              layout={dropletLayout}
              label="Scatter chart of droplet size at ignition against droplet size when the flame went out, by the gas the oxygen was mixed with"
            />
            <p className="quiet">
              {count(shown.length, 'test')} shown, all from {dropletFrom}. Colour and shape give the gas the oxygen was
              mixed with. Hover a mark for its test. NASA's column is “Visible flame extinction diameter”; it is read
              here as the droplet's size at that moment.
            </p>
            {shown.some(burnedOn) && (
              <p className="quiet">
                In {count(shown.filter(burnedOn).length, 'test')} shown here the visible flame went out part-way, yet
                the droplet went on burning until it was gone. The domain lead reads this as continued
                low-temperature burning, the cool-flame behaviour first seen in FLEX. Their hover text says so.
              </p>
            )}
          </>
        )}
      </div>

      <div className="panel">
        <p className="eyebrow"><b>03</b> Measured results · burn time</p>
        <h2>How long the flames burned, by fuel</h2>
        <p className="lede">
          Each dot is one test. The box above the dots covers the middle half of that fuel's tests, the line
          inside it is the median, and the thin line runs from the shortest burn to the longest. The grey number
          beside a fuel is its number of tests. Compare fuels inside one experiment only: a droplet, a gas burner and a sheet of fabric are different
          set-ups, so their burn times do not share a scale.
        </p>
        {burnTotal === 0 && <p className="quiet">No test on file records a burn time.</p>}
        {burnCharts.map(({ group, traces, layout }) => (
          <div key={group.id} className="subplot">
            <h3>{group.title}</h3>
            <Plot data={traces} layout={layout} label={`Box chart of burn time by fuel for ${group.title}`} />
            <p className="quiet">
              {count(group.total, 'test')} with a burn time.
              {group.unlit > 0 && ` ${count(group.unlit, 'test')} at 0 seconds did not light.`}
            </p>
          </div>
        ))}
        {burnFew.length > 0 && (
          <p className="quiet">
            Too few tests to chart:{' '}
            {burnFew
              .map((g) => `${g.title} (${g.rows.map((r) => `${formula(r.fuel)}: ${r.tests.map((x) => fmt(x.s)).join(' and ')} s`).join('; ')})`)
              .join(', ')}
            .
          </p>
        )}
      </div>
    </section>
  );
}

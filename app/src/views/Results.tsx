import { useMemo, useState } from 'react';
import Plot from '../components/Plot';
import { name } from '../data';
import { formula } from '../format';
import { toPsi } from '../habitats';
import type { Dataset, FuelPhase, TestRow } from '../types';

// Charts of measured results, for tests the atmosphere map cannot say much
// about. Every value comes from the optional `extra` fields of a test row.

const INK = '#16202e';
const SURFACE = '#fcfcfb';
const GRID = '#e6e9ed';
const BASE = {
  paper_bgcolor: SURFACE,
  plot_bgcolor: SURFACE,
  font: { family: '"IBM Plex Sans", system-ui, sans-serif', size: 13, color: INK },
  hoverlabel: { bgcolor: '#ffffff', bordercolor: '#c9d0d8', font: { color: INK, size: 13 }, align: 'left' },
};

const fmt = (n: number, d = 1) => n.toFixed(d).replace(/\.0$/, '');
const count = (n: number, word: string) => `${n.toLocaleString('en-US')} ${word}${n === 1 ? '' : 's'}`;
const median = (sorted: number[]) => {
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
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

// ---- Burn time by fuel ------------------------------------------------------

const PHASE_COLOR: Record<FuelPhase, string> = { solid: '#2a78d6', liquid: '#eb6834', gas: '#1baf7a', other: '#8a94a1' };
const CHART_FROM = 10; // investigations with fewer timed tests are listed in words

interface FuelRow {
  fuel: string;
  phase: FuelPhase;
  times: number[]; // seconds, sorted
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
        hovertext: rows.map(({ t, start, end }) =>
          [
            `<b>${title(t.investigation_id)} test ${t.test_label}</b>`,
            `${t.fuel_material ?? 'Fuel not recorded'}: ${fmt(start, 2)} mm at ignition, ${fmt(end, 2)} mm when the flame went out`,
            t.o2_pct != null && t.pressure_kpa != null
              ? `${fmt(t.o2_pct)}% oxygen at ${fmt(toPsi(t.pressure_kpa))} psi${t.diluent ? `, ${formula(t.diluent)}` : ''}`
              : null,
            `How it ended: ${ENDED[t.outcome]}`,
          ]
            .filter(Boolean)
            .join('<br>'),
        ),
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
      xaxis: { title: { text: 'Droplet size at ignition (mm)' }, rangemode: 'tozero', gridcolor: GRID, zeroline: false },
      yaxis: { title: { text: 'Droplet size when the flame went out (mm)' }, rangemode: 'tozero', gridcolor: GRID, zeroline: false },
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
      const row = fuels.get(key) ?? { fuel: key, phase: t.fuel_phase, times: [] };
      fuels.set(key, row);
      row.times.push(s);
    }
    return [...groups].map(([id, fuels]) => {
      const rows = [...fuels.values()];
      rows.forEach((r) => r.times.sort((a, b) => a - b));
      rows.sort((a, b) => median(b.times) - median(a.times));
      const all = rows.flatMap((r) => r.times);
      return { id, title: title(id), rows, total: all.length, unlit: all.filter((s) => s === 0).length };
    }).sort((a, b) => b.total - a.total);
  }, [data, byId]);

  const burnCharts = useMemo(
    () =>
      burn.filter((g) => g.total >= CHART_FROM).map((g) => ({
        group: g,
        // Plotly stacks horizontal boxes from the bottom, so the list is reversed.
        traces: [...g.rows].reverse().map((r) => ({
          type: 'box', orientation: 'h', name: `${formula(r.fuel)} (${r.times.length})`, x: r.times,
          boxpoints: 'all', jitter: 0.6, pointpos: 0, hoverinfo: 'x',
          marker: { color: PHASE_COLOR[r.phase], size: 4, opacity: 0.45 },
          line: { color: PHASE_COLOR[r.phase], width: 1.5 }, fillcolor: 'rgba(22, 32, 46, 0.04)',
        })),
        layout: {
          ...BASE,
          height: g.rows.length * 46 + 76,
          margin: { l: 150, r: 24, t: 8, b: 52 },
          showlegend: false,
          xaxis: { title: { text: 'Burn time (seconds)' }, rangemode: 'tozero', gridcolor: GRID, zeroline: false },
          yaxis: { automargin: true },
        },
      })),
    [burn],
  );
  const burnFew = burn.filter((g) => g.total < CHART_FROM);
  const burnTotal = burn.reduce((s, g) => s + g.total, 0);

  return (
    <section className="results">
      <div className="panel">
        <h2>How much of a droplet burns before the flame goes out</h2>
        <p className="lede">
          Each mark is one fuel droplet. The further a mark sits below the dashed line, the more of the droplet
          burned before its flame went out. A suppressant that works puts flames out early, so its marks sit
          closer to the line.
        </p>
        {droplets.length === 0 && <p className="quiet">No test on file gives both droplet sizes.</p>}
        {droplets.length > 0 && (
          <>
            <div className="chips" role="group" aria-label="Filter by fuel">
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
          </>
        )}
      </div>

      <div className="panel">
        <h2>How long the flames burned, by fuel</h2>
        <p className="lede">
          Each dot is one test and each box covers the middle half of its fuel's tests, with a line at the median.
          Compare fuels inside one experiment only: a droplet, a gas burner and a sheet of fabric are different
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
              .map((g) => `${g.title} (${g.rows.map((r) => `${formula(r.fuel)}: ${r.times.map((s) => fmt(s)).join(' and ')} s`).join('; ')})`)
              .join(', ')}
            .
          </p>
        )}
      </div>
    </section>
  );
}

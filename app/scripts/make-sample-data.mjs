// Generates PLACEHOLDER dashboard data so the UI can be built before the real
// pipeline output exists. Run from app/:  node scripts/make-sample-data.mjs
//
// What is real and what is not:
// - investigations.json: IDs, names, hardware and fuel phase come from the PSI
//   repository and the challenge brief. The "role" tags are a first draft for
//   the domain lead to correct.
// - tests.json: only the five BASS-II rows marked sample:false are copied from
//   the PSI experimental table. Every other row is generated at random inside
//   a plausible range and is marked sample:true. Do not present those rows as
//   NASA results. Replace this file with the pipeline output.
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const outDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'data');
const ATM_KPA = 101.325;
const url = (id) => `https://psi.nasa.gov/physci/repo/data/investigations/${id}`;

// [id, acronym, title, project_type, role, fuel_phase, hardware, related, doi]
const rows = [
  ['PSI-26', 'BASS', 'Burning and Suppression of Solids', 'flight', 'prevention', 'solid', 'MSG flow duct', ['PSI-25', 'PSI-107'], null],
  ['PSI-25', 'BASS-II', 'Burning and Suppression of Solids - II', 'flight', 'prevention', 'solid', 'MSG flow duct', ['PSI-26'], '10.60555/4qc4-de67'],
  ['PSI-98', 'Saffire-I', 'Spacecraft Fire Safety Experiment-I', 'flight', 'prevention', 'solid', 'Cygnus vehicle', ['PSI-99', 'PSI-100', 'PSI-26'], null],
  ['PSI-99', 'Saffire-II', 'Spacecraft Fire Experiment-II', 'flight', 'prevention', 'solid', 'Cygnus vehicle', ['PSI-98', 'PSI-100'], null],
  ['PSI-100', 'Saffire-III', 'Spacecraft Fire Experiment-III', 'flight', 'prevention', 'solid', 'Cygnus vehicle', ['PSI-98', 'PSI-99'], null],
  ['PSI-20', 'ACME BRE', 'ACME - Burning Rate Emulator', 'flight', 'prevention', 'gas', 'CIR / ACME', [], null],
  ['PSI-102', 'SAME', 'Smoke and Aerosol Measurement Experiment', 'flight', 'detection', 'solid', 'MSG', ['PSI-101'], null],
  ['PSI-101', 'SAME-R', 'Smoke Aerosol Measurement Experiment - Reflight', 'flight', 'detection', 'solid', 'MSG', ['PSI-102'], null],
  ['PSI-47', 'DAFT / DAFT-2', 'Dust and Aerosol Measurement Feasibility Test', 'flight', 'detection', 'other', 'ISS cabin', ['PSI-102'], null],
  ['PSI-69', 'FLEX', 'Flame Extinguishment Experiment', 'flight', 'suppression', 'liquid', 'CIR / MDCA', [], '10.60555/mbq8-0451'],
  ['PSI-68', 'FLEX-2', 'Flame Extinguishment Experiment - 2', 'flight', 'suppression', 'liquid', 'CIR / MDCA', ['PSI-69'], null],
  ['PSI-39', 'CFI', 'Cool Flames Investigation', 'flight', 'fundamentals', 'liquid', 'CIR / MDCA', ['PSI-69'], null],
  ['PSI-159', 'ACME CFI-G', 'ACME - Cool Flames Investigation with Gases', 'flight', 'fundamentals', 'gas', 'CIR / ACME', [], null],
  ['PSI-10', 'ACME Flame Design', 'ACME - Flame Design', 'flight', 'fundamentals', 'gas', 'CIR / ACME', [], null],
  ['PSI-21', 'ACME CLD Flame', 'ACME - Coflow Laminar Diffusion Flame', 'flight', 'fundamentals', 'gas', 'CIR / ACME', [], null],
  ['PSI-22', 'ACME E-FIELD', 'ACME - Electric-Field Effects on Laminar Diffusion Flames', 'flight', 'fundamentals', 'gas', 'CIR / ACME', ['PSI-23'], null],
  ['PSI-23', 'ACME s-Flame', 'ACME - Structure and Response of Spherical Diffusion Flames', 'flight', 'fundamentals', 'gas', 'CIR / ACME', [], null],
  ['PSI-107', 'SPICE', 'Smoke Point In Co-flow Experiment', 'flight', 'fundamentals', 'gas', 'MSG flow duct', [], null],
  ['PSI-106', 'SLICE', 'Structure and Liftoff In Combustion Experiment', 'flight', 'fundamentals', 'gas', 'MSG flow duct', ['PSI-107'], null],
  ['PSI-117', '', 'Effect of External Thermo-Convective Perturbation on Cool Flame Dynamics (CFD)', 'ground', 'fundamentals', 'liquid', null, ['PSI-69', 'PSI-142'], null],
  ['PSI-142', '', 'Cool Flame Transitions at Radiation / Stretch Extinction Using Counterflow Flames', 'ground', 'fundamentals', 'liquid', null, ['PSI-69'], null],
  ['PSI-62', '', 'Concurrent Flame Spread Modeling with Comparison to BASS Experiments', 'ground', 'prevention', 'solid', null, ['PSI-25', 'PSI-26'], null],
  ['PSI-60', '', 'Computational and Experimental Analysis of SPICE Data Sets', 'ground', 'fundamentals', 'gas', null, ['PSI-107'], null],
  ['PSI-115', '', 'Modeling and Simulation of Smoke Generation in Microgravity (SAME data)', 'ground', 'detection', 'solid', null, ['PSI-102'], null],
];

const investigations = rows.map(
  ([id, acronym, title, project_type, role, fuel_phase, hardware, related, doi]) => ({
    id, acronym: acronym || null, title, project_type, role, fuel_phase, hardware, related, doi, url: url(id),
  }),
);

// Copied from the PSI-25 experimental table. That table has no pressure
// column; ISS cabin pressure is assumed, and the note says so.
const bassIIFuel = [
  ['B1', '100 micron PMMA film 2 cm wide', 22.2],
  ['B2', '200 micron PMMA film 2 cm wide', 22.2],
  ['B3', '100 micron film 2 cm wide', 22.2],
  ['B4', '300 micron PMMA film 2 cm wide', 22.2],
  ['B5', '200 micron 2 cm wide', 22.2],
];
const tests = bassIIFuel.map(([label, fuel, o2], i) => ({
  id: `PSI-25:${label}`, investigation_id: 'PSI-25', test_label: label, fuel_material: fuel,
  fuel_phase: 'solid', o2_pct: o2, pressure_kpa: ATM_KPA, flow_cm_s: null, diluent: 'N2',
  outcome: 'unknown', note: 'Pressure not in the source table; ISS cabin pressure assumed.',
  source: { file: 'PSI-25_Experimental table_BASS-II.csv', row: i + 1 }, sample: false,
}));

// Small seeded generator so the placeholder file is identical on every run.
let seed = 20261114;
const rand = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const between = (lo, hi) => lo + rand() * (hi - lo);
const round1 = (x) => Math.round(x * 10) / 10;

// [investigation, phase, rows, O2 % range, pressure atm range, flow cm/s range, diluents]
const plan = [
  ['PSI-25', 'solid', 30, [14, 21], [1, 1], [1, 40], ['N2']],
  ['PSI-26', 'solid', 20, [16, 21], [1, 1], [2, 30], ['N2']],
  ['PSI-98', 'solid', 4, [21, 21], [1, 1], [20, 25], ['N2']],
  ['PSI-99', 'solid', 9, [21, 21], [1, 1], [20, 25], ['N2']],
  ['PSI-100', 'solid', 4, [21, 21], [1, 1], [20, 30], ['N2']],
  ['PSI-102', 'solid', 12, [21, 21], [1, 1], [0, 8], ['N2']],
  ['PSI-101', 'solid', 12, [21, 21], [1, 1], [0, 8], ['N2']],
  ['PSI-69', 'liquid', 40, [10, 40], [0.5, 3], [0, 0], ['N2', 'CO2', 'He']],
  ['PSI-68', 'liquid', 40, [10, 40], [0.5, 3], [0, 3], ['N2', 'CO2', 'He']],
  ['PSI-39', 'liquid', 20, [15, 35], [0.25, 5], [0, 0], ['N2']],
  ['PSI-20', 'gas', 20, [21, 40], [0.5, 1], [0, 0], ['N2']],
  ['PSI-23', 'gas', 15, [21, 40], [0.5, 1], [0, 0], ['N2']],
  ['PSI-10', 'gas', 15, [21, 40], [0.5, 1], [0, 0], ['N2']],
  ['PSI-107', 'gas', 12, [21, 21], [1, 1], [5, 50], ['N2']],
  ['PSI-106', 'gas', 12, [21, 21], [1, 1], [5, 50], ['N2']],
];
for (const [inv, phase, n, o2, atm, flow, diluents] of plan) {
  for (let i = 1; i <= n; i++) {
    const o2Pct = round1(between(...o2));
    const p = between(...atm);
    // Placeholder rule only: low oxygen at low pressure goes out more often.
    const sustained = rand() < Math.min(0.95, Math.max(0.05, (o2Pct - 12) / 12 + (p - 1) * 0.1));
    tests.push({
      id: `${inv}:S${i}`, investigation_id: inv, test_label: `S${i}`, fuel_material: null,
      fuel_phase: phase, o2_pct: o2Pct, pressure_kpa: round1(p * ATM_KPA),
      flow_cm_s: flow[1] === 0 ? 0 : round1(between(...flow)),
      diluent: diluents[Math.floor(rand() * diluents.length)],
      outcome: sustained ? 'sustained' : 'extinguished', note: null, source: null, sample: true,
    });
  }
}

const meta = {
  generated_at: '2026-11-14',
  generator: 'app/scripts/make-sample-data.mjs',
  sample_rows: tests.filter((t) => t.sample).length,
  total_rows: tests.length,
};
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, 'investigations.json'), JSON.stringify({ meta: { ...meta, roles_reviewed: false }, investigations }, null, 1));
writeFileSync(join(outDir, 'tests.json'), JSON.stringify({ meta, tests }, null, 0));
console.log(`Wrote ${investigations.length} investigations and ${tests.length} tests (${meta.sample_rows} placeholder).`);

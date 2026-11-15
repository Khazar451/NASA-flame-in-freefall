// Deterministic relevance score. No model is involved: every number on the
// ranking page can be traced to the settings in habitats.ts.
import {
  EVIDENCE_FULL_AT, FUEL_OVERRIDES, FUEL_RELEVANCE, O2_REACH_PCT, PRESSURE_REACH_PSI, ROLE_RELEVANCE, toPsi,
  type Habitat, type PartKey,
} from './habitats';
import type { Investigation, TestRow } from './types';

const gap = (x: number, [lo, hi]: [number, number]) => (x < lo ? lo - x : x > hi ? x - hi : 0);

export const hasAtmosphere = (t: TestRow): t is TestRow & { o2_pct: number; pressure_kpa: number } =>
  t.o2_pct != null && t.pressure_kpa != null;

export const insideHabitat = (t: TestRow, h: Habitat) =>
  hasAtmosphere(t) && gap(t.o2_pct, h.o2) === 0 && gap(toPsi(t.pressure_kpa), h.psi) === 0;

/** 1 inside the habitat's range, falling to 0 at the reach distance. */
export function closeness(t: TestRow, h: Habitat): number {
  if (!hasAtmosphere(t)) return 0;
  const o2 = Math.max(0, 1 - gap(t.o2_pct, h.o2) / O2_REACH_PCT);
  const p = Math.max(0, 1 - gap(toPsi(t.pressure_kpa), h.psi) / PRESSURE_REACH_PSI);
  return o2 * p;
}

export interface Scored {
  inv: Investigation;
  total: number; // every test row on file
  tests: number; // rows with oxygen and pressure
  inside: number; // rows inside the habitat's range
  closest: TestRow | null;
  parts: Record<PartKey, number>; // each 0..1
  score: number; // 0..100
  // A ground study with no tests of its own is scored on the flight tests it
  // analyses; these are the investigations they come from (see dataSources).
  dataFrom: Investigation[];
}

export function scoreInvestigation(
  inv: Investigation, tests: TestRow[], h: Habitat, weights: Record<PartKey, number>,
  dataFrom: Investigation[] = [],
): Scored {
  const usable = tests.filter(hasAtmosphere);
  const ranked = usable.map((t) => ({ t, c: closeness(t, h) })).sort((a, b) => b.c - a.c);
  // Average of the closest quarter of tests, so a study is credited for the
  // part of its test matrix that reaches this habitat.
  const top = ranked.slice(0, Math.max(1, Math.ceil(ranked.length / 4)));
  const parts: Record<PartKey, number> = {
    atmosphere: ranked.length ? top.reduce((s, r) => s + r.c, 0) / top.length : 0,
    fuel: FUEL_OVERRIDES[inv.id]?.value ?? FUEL_RELEVANCE[inv.fuel_phase],
    role: ROLE_RELEVANCE[inv.role],
    // Every test on file counts here, including those whose source table does
    // not give oxygen or pressure: they are still data to work with.
    evidence: Math.min(1, Math.log10(1 + tests.length) / Math.log10(1 + EVIDENCE_FULL_AT)),
  };
  const keys = Object.keys(parts) as PartKey[];
  const total = keys.reduce((s, k) => s + weights[k], 0);
  const score = total > 0 ? (100 * keys.reduce((s, k) => s + weights[k] * parts[k], 0)) / total : 0;
  return {
    inv,
    total: tests.length,
    tests: usable.length,
    inside: usable.filter((t) => insideHabitat(t, h)).length,
    closest: ranked[0]?.t ?? null,
    parts,
    score,
    dataFrom,
  };
}

/**
 * The related flight investigations whose tests a ground study analyses. Empty
 * for flight experiments and for ground studies with test rows of their own.
 */
export function dataSources(
  inv: Investigation, byInv: Map<string, TestRow[]>, byId: Map<string, Investigation>,
): Investigation[] {
  if (inv.project_type !== 'ground' || byInv.has(inv.id)) return [];
  return inv.related
    .map((id) => byId.get(id))
    .filter((r): r is Investigation => !!r && r.project_type === 'flight' && byInv.has(r.id));
}

export function rank(
  investigations: Investigation[], tests: TestRow[], h: Habitat, weights: Record<PartKey, number>,
): Scored[] {
  const byInv = new Map<string, TestRow[]>();
  for (const t of tests) {
    const list = byInv.get(t.investigation_id);
    if (list) list.push(t);
    else byInv.set(t.investigation_id, [t]);
  }
  const byId = new Map(investigations.map((i) => [i.id, i]));
  return investigations
    .map((inv) => {
      const from = dataSources(inv, byInv, byId);
      const tests = from.length ? from.flatMap((r) => byInv.get(r.id)!) : byInv.get(inv.id) ?? [];
      return scoreInvestigation(inv, tests, h, weights, from);
    })
    .sort((a, b) => b.score - a.score || a.inv.id.localeCompare(b.inv.id));
}

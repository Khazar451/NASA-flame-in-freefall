// Deterministic relevance score. No model is involved: every number on the
// ranking page can be traced to the settings in habitats.ts.
import {
  EVIDENCE_FULL_AT, FUEL_RELEVANCE, O2_REACH_PCT, PRESSURE_REACH_PSI, ROLE_RELEVANCE, toPsi,
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
  tests: number; // rows with oxygen and pressure
  inside: number; // rows inside the habitat's range
  closest: TestRow | null;
  parts: Record<PartKey, number>; // each 0..1
  score: number; // 0..100
}

export function scoreInvestigation(
  inv: Investigation, tests: TestRow[], h: Habitat, weights: Record<PartKey, number>,
): Scored {
  const usable = tests.filter(hasAtmosphere);
  const ranked = usable.map((t) => ({ t, c: closeness(t, h) })).sort((a, b) => b.c - a.c);
  // Average of the closest quarter of tests, so a study is credited for the
  // part of its test matrix that reaches this habitat.
  const top = ranked.slice(0, Math.max(1, Math.ceil(ranked.length / 4)));
  const parts: Record<PartKey, number> = {
    atmosphere: ranked.length ? top.reduce((s, r) => s + r.c, 0) / top.length : 0,
    fuel: FUEL_RELEVANCE[inv.fuel_phase],
    role: ROLE_RELEVANCE[inv.role],
    evidence: Math.min(1, Math.log10(1 + usable.length) / Math.log10(1 + EVIDENCE_FULL_AT)),
  };
  const keys = Object.keys(parts) as PartKey[];
  const total = keys.reduce((s, k) => s + weights[k], 0);
  const score = total > 0 ? (100 * keys.reduce((s, k) => s + weights[k] * parts[k], 0)) / total : 0;
  return {
    inv,
    tests: usable.length,
    inside: usable.filter((t) => insideHabitat(t, h)).length,
    closest: ranked[0]?.t ?? null,
    parts,
    score,
  };
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
  return investigations
    .map((inv) => scoreInvestigation(inv, byInv.get(inv.id) ?? [], h, weights))
    .sort((a, b) => b.score - a.score || a.inv.id.localeCompare(b.inv.id));
}

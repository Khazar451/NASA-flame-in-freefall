// Habitat atmospheres and scoring settings. The numbers in HABITATS come from
// the challenge brief.
// Everything under "Scoring settings" is a first draft for the domain lead to adjust.
import type { FuelPhase, Role } from './types';

export const KPA_PER_PSI = 6.894757;
export const toPsi = (kpa: number) => kpa / KPA_PER_PSI;

export interface Habitat {
  id: string;
  label: string;
  short: string;
  o2: [number, number]; // % by volume, min to max
  psi: [number, number]; // total pressure, min to max
  basis: string;
}

export const HABITATS: Habitat[] = [
  {
    id: 'cabin',
    label: 'Spacecraft or station cabin',
    short: 'Cabin (Earth-like)',
    o2: [21, 21],
    psi: [14.7, 14.7],
    basis: 'Sea-level air: 21% oxygen at 14.7 psi.',
  },
  {
    id: 'lunar',
    label: 'Lunar base or pressurized rover',
    short: 'Lunar base / rover',
    // The brief's range, kept open at the top: for fire safety, tests in more
    // oxygen than a habitat plans for are the harsher case, not irrelevant ones.
    // NASA's own planned atmospheres are marked separately (NASA_PLANNED below).
    o2: [30, 100],
    psi: [4.3, 10.2],
    basis:
      "Challenge brief: likely 30% oxygen or more at 10.2 psi or less, between spacesuit and Earth pressure. NASA's own reports name 34% at 8.2 psi and 26.5% at 10.2 psi; both are marked on the map.",
  },
  {
    id: 'suit',
    label: 'Spacesuit',
    short: 'Spacesuit',
    o2: [100, 100],
    psi: [4.3, 4.3],
    basis: 'Challenge brief: 100% oxygen at 4.3 psi.',
  },
];

// The atmospheres NASA names for future spacecraft and habitats, beside cabin
// air. Source: ACME BRE final report, PSI-20, p. 5: "NASA-considered atmosphere
// for human space missions … 14.7 psia, 21 % oxygen; 10.2 psia, 26.5 %; and 8.2
// psia, 34 % oxygen" (also ACME's requirements document, PSI-10, p. 82). Drawn on
// the map as reference points.
const PLANNED_SOURCE = 'Source: ACME BRE final report (PSI-20), p. 5.';
export const NASA_PLANNED = [
  { o2: 34, psi: 8.2, label: 'NASA planned: 34% at 8.2 psi', basis: `An atmosphere NASA is considering for human space missions: 34% oxygen at 8.2 psi (56.5 kPa). ${PLANNED_SOURCE}` },
  { o2: 26.5, psi: 10.2, label: 'NASA planned: 26.5% at 10.2 psi', basis: `An atmosphere NASA is considering for human space missions: 26.5% oxygen at 10.2 psi (70.3 kPa). It lies just below the brief's 30% oxygen. ${PLANNED_SOURCE}` },
];
// Reference oxygen for the "richer than planned" note on the coverage panel.
export const PLANNED_MAX_O2 = Math.max(...NASA_PLANNED.map((p) => p.o2));

// ---- Scoring settings (draft) ----------------------------------------------

// A test gets full atmosphere credit inside the habitat's range. Credit falls
// in a straight line to zero this far outside it.
export const O2_REACH_PCT = 15;
export const PRESSURE_REACH_PSI = 6;

export const FUEL_RELEVANCE: Record<FuelPhase, number> = {
  solid: 1, // cabin fires start in solid materials: fabric, plastic, wiring
  liquid: 0.6,
  gas: 0.4,
  other: 0.5,
};

// Investigations whose fuel counts differently from their fuel phase, and why.
export const FUEL_OVERRIDES: Record<string, { value: number; why: string }> = {
  // ACME BRE burns gas on a porous burner built to behave like a burning solid.
  'PSI-20': { value: 0.85, why: 'Its gas burner emulates a burning solid, so its fuel counts nearly as a solid.' },
};

export const ROLE_RELEVANCE: Record<Role, number> = {
  prevention: 1,
  detection: 1,
  suppression: 1,
  fundamentals: 0.4,
};

// Tests this far above a range's top pressure are not counted as inside it, but
// the map names them. FLEX ran at 0.7 atmospheres, which it measured as 10.3 to
// 10.5 psi: just over the brief's 10.2 psi. The domain lead chose to keep the
// brief's limit and say so, not to widen the range.
export const EDGE_NOTE_PSI = 0.3;

// Number of tests at which the "evidence" part reaches full credit.
export const EVIDENCE_FULL_AT = 150;

export type PartKey = 'atmosphere' | 'fuel' | 'role' | 'evidence';

export const PARTS: { key: PartKey; label: string; help: string }[] = [
  { key: 'atmosphere', label: 'Atmosphere match', help: 'How close its closest tests come to this oxygen level and pressure.' },
  { key: 'fuel', label: 'Fuel type', help: 'Solid materials count most, then liquids, then gases. A gas burner that emulates a solid counts nearly as a solid.' },
  { key: 'role', label: 'Fire-safety role', help: 'Prevention, detection and suppression studies count more than basic flame science.' },
  { key: 'evidence', label: 'Amount of data', help: 'More test runs give more to work with.' },
];

export const DEFAULT_WEIGHTS: Record<PartKey, number> = {
  atmosphere: 40,
  fuel: 25,
  role: 25,
  evidence: 10,
};

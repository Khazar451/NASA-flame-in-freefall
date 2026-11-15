// Habitat atmospheres and scoring settings. The numbers in HABITATS come from
// the challenge brief, with the lunar range's oxygen capped as noted there.
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
    // The brief says 30% oxygen or more. No crewed habitat or rover is designed
    // for pure oxygen, so the range stops at 36%: NASA's planned exploration
    // atmosphere (EXPLORATION below) plus a small margin.
    o2: [30, 36],
    psi: [4.3, 10.2],
    basis:
      "Challenge brief: 30% oxygen or more at 10.2 psi or less. Capped at 36% oxygen, just above NASA's planned exploration atmosphere of 34% at 8.2 psi; pure oxygen is for spacesuits only.",
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

// NASA's planned atmosphere for exploration habitats and rovers (Exploration
// Atmospheres Working Group). Drawn on the map as a reference point.
export const EXPLORATION = { o2: 34, psi: 8.2, label: 'NASA exploration plan', basis: "NASA's planned exploration atmosphere: 34% oxygen at 8.2 psi (56.5 kPa)." };

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

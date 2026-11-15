// The data contract between the pipeline and the dashboard.
// Keep this file in step with docs/data-contract.md.

export type Role = 'prevention' | 'detection' | 'suppression' | 'fundamentals';
export type FuelPhase = 'solid' | 'liquid' | 'gas' | 'other';
export type Outcome = 'sustained' | 'extinguished' | 'no_ignition' | 'unknown';

export interface Investigation {
  id: string; // PSI accession, e.g. "PSI-25"
  acronym: string | null;
  title: string;
  project_type: 'flight' | 'ground';
  role: Role;
  fuel_phase: FuelPhase;
  hardware: string | null;
  related: string[];
  doi: string | null;
  url: string;
  // Optional extras from the pipeline. Views show them when present.
  // hardware_family "smoke_aerosol" marks smoke experiments (see isSmokeStudy).
  hardware_family?: string | null;
  sub_area?: string | null;
  objectives?: string | null;
  earth_benefits?: string | null;
  n_publications?: number | null;
}

export interface TestRow {
  id: string;
  investigation_id: string;
  test_label: string;
  fuel_material: string | null;
  fuel_phase: FuelPhase;
  o2_pct: number | null; // ambient oxygen, % by volume
  pressure_kpa: number | null; // total pressure
  flow_cm_s: number | null;
  diluent: string | null;
  outcome: Outcome;
  note: string | null;
  source: { file: string; row: number } | null;
  sample: boolean; // true = placeholder row, not a NASA result
  // The fields whose value the pipeline assumed, not read from the source table.
  // Absent in placeholder data made by scripts/make-sample-data.mjs.
  assumed?: ('o2_pct' | 'pressure_kpa' | 'flow_cm_s')[];
  // Optional: values only some experiment families report, e.g. burn_time_s.
  extra?: Record<string, number | string | boolean | null> | null;
}

export interface Dataset {
  investigations: Investigation[];
  tests: TestRow[];
  sampleRows: number;
  rolesReviewed: boolean;
}

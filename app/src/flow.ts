// Air-flow regimes. With no buoyancy, the air that ventilation moves past a
// flame matters as much as its oxygen level, so the map can be filtered by it.
import type { Investigation, TestRow } from './types';

export type FlowRegime = 'still' | 'low' | 'vent' | 'unknown';

// Up to this speed a flame is in the weak-flow range where BASS-II found flames
// surviving at their lowest oxygen.
export const LOW_FLOW_MAX_CM_S = 5;

export const FLOW_REGIMES: { key: FlowRegime; label: string; help: string }[] = [
  { key: 'still', label: 'No forced flow', help: 'Sealed chamber with still air (ACME, droplet tests), or a flow of 0 cm/s.' },
  { key: 'low', label: `Weak flow (up to ${LOW_FLOW_MAX_CM_S} cm/s)`, help: 'Weak flow, where flames can survive at their lowest oxygen.' },
  { key: 'vent', label: `Ventilation (over ${LOW_FLOW_MAX_CM_S} cm/s)`, help: 'Flow like a cabin fan or duct.' },
  { key: 'unknown', label: 'Flow not recorded', help: 'The source table gives no air-flow speed.' },
];

// Hardware where the flame burns in still air: the table has no flow column
// because there is no forced flow.
const STILL_AIR_HARDWARE = new Set(['acme_gas_flames', 'mdca_droplets']);

export function flowRegime(t: TestRow, inv: Investigation | undefined): FlowRegime {
  if (t.flow_cm_s == null) return STILL_AIR_HARDWARE.has(inv?.hardware_family ?? '') ? 'still' : 'unknown';
  if (t.flow_cm_s === 0) return 'still';
  return t.flow_cm_s <= LOW_FLOW_MAX_CM_S ? 'low' : 'vent';
}

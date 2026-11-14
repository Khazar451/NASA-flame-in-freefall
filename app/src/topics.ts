// Topics that cut across the four fire-safety roles.
import type { Investigation } from './types';

// Cool flames are low-temperature flames, first seen in the FLEX droplet tests,
// that can go on burning after the visible flame is out. An investigation
// belongs here when NASA's own record names them: in its title, objectives or
// research impacts. Nothing is listed by hand.
const COOL_FLAME = /cool[\s-]?flames?/i;

export const isCoolFlameStudy = (inv: Investigation | undefined): boolean =>
  !!inv && [inv.title, inv.objectives, inv.earth_benefits].some((text) => !!text && COOL_FLAME.test(text));

// Chemical formulas arrive from the source tables as plain text (C12H26, CO2).
// Show their digits as subscripts. Only whole words made of element symbols
// and digits are touched, so "2 cm", "PMMA" and "1/4 rod" stay as they are.
const SUBSCRIPT = '₀₁₂₃₄₅₆₇₈₉';
const FORMULA = /\b(?:(?:He|Ar|C|H|N|O|S|F)\d*)+\b/g;

export const formula = (text: string) =>
  text.replace(FORMULA, (word) => word.replace(/\d/g, (d) => SUBSCRIPT[Number(d)]));

// When the pipeline fills in a value the source table does not give, it adds a
// sentence to the test's note: "Oxygen not in the source table; … assumed."
// These say which values that covers.
type Note = string | null | undefined;
export const oxygenAssumed = (note: Note) => /\boxygen not in the source table[^.]*assumed/i.test(note ?? '');
export const pressureAssumed = (note: Note) => /\bpressure not in the source table[^.]*assumed/i.test(note ?? '');

// "Oxygen and pressure assumed, not measured, for 12 of them", or null when
// every value in the group was measured.
export function assumedLine(notes: Note[]): string | null {
  const oxygen = notes.filter(oxygenAssumed).length;
  const pressure = notes.filter(pressureAssumed).length;
  if (oxygen + pressure === 0) return null;
  const what = oxygen > 0 && pressure > 0 ? 'Oxygen and pressure' : oxygen > 0 ? 'Oxygen' : 'Pressure';
  const affected = Math.max(oxygen, pressure);
  return `${what} assumed, not measured${affected < notes.length ? `, for ${affected} of them` : ''}`;
}

// Smoke experiments heat a material to make smoke for particle instruments.
// There is no flame, so "how the flame ended" does not apply to their tests.
export const isSmokeStudy = (inv: { hardware_family?: string | null } | undefined) =>
  inv?.hardware_family === 'smoke_aerosol';
export const SMOKE_NOTE = 'Smoke test: the material was heated to make smoke, so there is no flame outcome';

// Some rows are not single tests. ACME BRE's table gives no pressures, so the
// pipeline writes one row per atmosphere that its final report describes and
// marks it with extra.row_kind = "atmosphere". Such rows are drawn, but never
// counted as tests.
export const isReportSummary = (t: { extra?: Record<string, unknown> | null }) => t.extra?.row_kind === 'atmosphere';
export const SUMMARY_NOTE = 'Summary of one atmosphere from the final report, not a single test';
export const summaries = (n: number) => (n === 1 ? '1 report summary' : `${n} report summaries`);

export interface Numbered {
  intro: string; // text before point 1, may be empty
  items: string[];
}

// An item's text, without the "and" or comma that led into the next marker.
const tidy = (item: string) => item.trim().replace(/[,;]?\s+and$/, '').replace(/[,;]$/, '').trim();

// True when position `at` sits inside an open bracket, as the 1) in "(NASA-STD-6001 Test 1)" does.
const bracketed = (text: string, at: number) => {
  const before = text.slice(0, at);
  return before.lastIndexOf('(') > before.lastIndexOf(')');
};

// PSI records write lists inline: "… to: 1) first. 2) second" (also "1." and
// "(1)"). Returns the points, or null unless the text carries the markers
// 1, 2, 3… in order, all closed the same way. Requiring the sequence keeps
// "Test 1)" or a sentence ending in a number from being read as a list.
export function numberedList(text: string): Numbered | null {
  let best: [number, number][] | null = null; // per marker: where it starts, where its item starts
  for (const close of ['.', ')']) {
    const marker = (n: number) => new RegExp(`(^|\\s)\\(?${n}\\${close}\\s`, 'g');
    for (const first of text.matchAll(marker(1))) {
      if (bracketed(text, first.index + first[1].length)) continue;
      const cuts: [number, number][] = [[first.index + first[1].length, first.index + first[0].length]];
      for (let n = 2; ; n++) {
        const next = marker(n);
        next.lastIndex = cuts[cuts.length - 1][1];
        const m = next.exec(text);
        if (!m || bracketed(text, m.index + m[1].length)) break;
        cuts.push([m.index + m[1].length, m.index + m[0].length]);
      }
      // On a tie the later start wins: an earlier one skipped the real point 1.
      if (cuts.length >= 2 && (!best || cuts.length >= best.length)) best = cuts;
    }
  }
  if (!best) return null;
  const cuts = best;
  return {
    intro: text.slice(0, cuts[0][0]).trim(),
    items: cuts.map(([, from], i) => tidy(text.slice(from, i + 1 < cuts.length ? cuts[i + 1][0] : text.length))),
  };
}

// One look for every chart. The colours match the tokens in styles.css; Plotly
// draws on a canvas-like surface and cannot read CSS variables.
export const INK = '#151b26';
export const INK_2 = '#4d5664';
export const INK_3 = '#7a8290';
export const SURFACE = '#fbfaf6';
export const GRID = '#e7e3d8';
export const SANS = '"IBM Plex Sans", system-ui, sans-serif';
export const MONO = '"IBM Plex Mono", ui-monospace, monospace';

export const BASE = {
  paper_bgcolor: SURFACE,
  plot_bgcolor: SURFACE,
  font: { family: SANS, size: 13, color: INK },
  hoverlabel: { bgcolor: '#ffffff', bordercolor: INK, font: { family: SANS, color: INK, size: 13 }, align: 'left' },
};

// Axis numbers are set in the monospace face, like the figures elsewhere on the
// page, and the grid stays one step off the surface.
export const AXIS = {
  gridcolor: GRID,
  zeroline: false,
  tickfont: { family: MONO, size: 12, color: INK_2 },
};
// A drawn baseline with short ticks, for the axis a chart is read along.
export const BASELINE = { showline: true, linecolor: INK, linewidth: 1, ticks: 'outside', ticklen: 4, tickcolor: INK };

// A series colour at reduced strength, for fills behind a stroke of the same hue.
export const tint = (hex: string, alpha: number) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
};

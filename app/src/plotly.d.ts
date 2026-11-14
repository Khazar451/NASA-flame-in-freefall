// plotly.js-dist-min ships without types. We only call react/purge and listen
// for clicks, so a loose declaration is enough.
declare module 'plotly.js-dist-min' {
  const Plotly: {
    react(el: HTMLElement, data: unknown[], layout?: unknown, config?: unknown): Promise<unknown>;
    purge(el: HTMLElement): void;
  };
  export default Plotly;
}

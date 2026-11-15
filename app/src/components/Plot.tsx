import { useEffect, useRef } from 'react';
import Plotly from 'plotly.js-dist-min';

interface Props {
  data: unknown[];
  layout: Record<string, unknown>;
  onPointClick?: (customdata: unknown) => void;
  label: string;
}

/** Thin wrapper: Plotly draws into the div, React only owns the div. */
export default function Plot({ data, layout, onPointClick, label }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const click = useRef(onPointClick);
  click.current = onPointClick;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    void Plotly.react(el, data, layout, { responsive: true, displaylogo: false, displayModeBar: false }).then(() => {
      const plot = el as unknown as {
        removeAllListeners?: (e: string) => void;
        on?: (e: string, fn: (ev: { points: { customdata?: unknown }[] }) => void) => void;
      };
      plot.removeAllListeners?.('plotly_click');
      plot.on?.('plotly_click', (ev) => click.current?.(ev.points[0]?.customdata));
    });
  }, [data, layout]);

  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el) Plotly.purge(el);
    };
  }, []);

  return <div ref={ref} className="plot" role="img" aria-label={label} />;
}

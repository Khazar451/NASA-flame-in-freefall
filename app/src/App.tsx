import { useEffect, useState } from 'react';
import { useDataset } from './data';
import AtmosphereMap from './views/AtmosphereMap';
import Ranking from './views/Ranking';
import Ask from './views/Ask';

const TABS = [
  { id: 'map', label: 'Atmosphere map' },
  { id: 'ranking', label: 'Ranking' },
  { id: 'ask', label: 'Ask' },
] as const;
type TabId = (typeof TABS)[number]['id'];

const tabFromHash = (): TabId => {
  const id = window.location.hash.slice(1);
  return TABS.some((t) => t.id === id) ? (id as TabId) : 'map';
};

export default function App() {
  const { data, error } = useDataset();
  const [tab, setTab] = useState<TabId>(tabFromHash);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1>Flame in Freefall</h1>
          <p>NASA microgravity combustion experiments, read for crew fire safety</p>
        </div>
        <nav aria-label="Views">
          {TABS.map((t) => (
            <a key={t.id} href={`#${t.id}`} aria-current={tab === t.id ? 'page' : undefined}>
              {t.label}
            </a>
          ))}
        </nav>
      </header>

      {data && data.sampleRows > 0 && (
        <p className="notice" role="status">
          <strong>Placeholder data.</strong> {data.sampleRows} of {data.tests.length} test rows are randomly
          generated stand-ins, not NASA results. Run <code>npm run sync-data</code> to load the pipeline output
          from <code>data/out/</code>.
        </p>
      )}

      <main>
        {error && (
          <p className="notice error" role="alert">
            {error} Run <code>npm run sync-data</code> to copy investigations.json and tests.json from{' '}
            <code>data/out/</code>.
          </p>
        )}
        {!data && !error && <p className="quiet">Loading data…</p>}
        {data && tab === 'map' && <AtmosphereMap data={data} />}
        {data && tab === 'ranking' && <Ranking data={data} />}
        {tab === 'ask' && <Ask />}
      </main>
    </div>
  );
}

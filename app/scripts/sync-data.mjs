// Copies the pipeline output (data/out/ at the repo root) into public/data/,
// where the dashboard reads it. Runs before `npm run dev` and `npm run build`,
// so the app always shows the data that is in the repo.
//
// The pipeline never writes into app/; this script is the only link.
import { copyFileSync, existsSync, mkdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const appDir = join(dirname(fileURLToPath(import.meta.url)), '..');
const from = join(appDir, '..', 'data', 'out');
const to = join(appDir, 'public', 'data');

// The files the dashboard loads. Add coverage.json / varied.json here when a
// view starts reading them.
const FILES = ['investigations.json', 'tests.json'];

const missing = FILES.filter((f) => !existsSync(join(from, f)));
if (missing.length > 0) {
  const have = FILES.every((f) => existsSync(join(to, f)));
  if (have) {
    console.warn(`sync-data: ${missing.join(', ')} not found in data/out/. Keeping the files already in public/data/.`);
    process.exit(0);
  }
  console.error(
    `sync-data: ${missing.join(', ')} not found in data/out/, and public/data/ is empty.\n` +
      'Pull the latest main (the pipeline output is committed there), or run `npm run sample-data` for placeholder data.',
  );
  process.exit(1);
}

mkdirSync(to, { recursive: true });
for (const f of FILES) {
  copyFileSync(join(from, f), join(to, f));
  console.log(`sync-data: ${f} (${Math.round(statSync(join(to, f)).size / 1024)} kB)`);
}

import { useEffect, useState } from 'react';
import type { Dataset, Investigation, TestRow } from './types';

const DATA_DIR = `${import.meta.env.BASE_URL}data`;

async function getJson<T>(file: string): Promise<T> {
  const res = await fetch(`${DATA_DIR}/${file}`);
  if (!res.ok) throw new Error(`Could not load ${file} (HTTP ${res.status}).`);
  return res.json() as Promise<T>;
}

export async function loadDataset(): Promise<Dataset> {
  const [inv, tests] = await Promise.all([
    getJson<{ meta?: { roles_reviewed?: boolean }; investigations: Investigation[] }>('investigations.json'),
    getJson<{ tests: TestRow[] }>('tests.json'),
  ]);
  return {
    investigations: inv.investigations,
    tests: tests.tests,
    sampleRows: tests.tests.filter((t) => t.sample).length,
    rolesReviewed: inv.meta?.roles_reviewed === true,
  };
}

export function useDataset() {
  const [data, setData] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    loadDataset()
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);
  return { data, error };
}

export const name = (inv: Investigation) => inv.acronym ?? inv.id;

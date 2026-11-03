import { useQueryClient } from '@tanstack/react-query';
import { collection, limit, onSnapshot, orderBy, query } from 'firebase/firestore';
import { useEffect, useState } from 'react';

import { auth, db } from '../lib/firebase';

/** True when the Firebase custom-token sign-in succeeded (live listeners available). */
export function useIsLive(): boolean {
  const [live, setLive] = useState(!!auth.currentUser);
  useEffect(() => auth.onAuthStateChanged((u) => setLive(!!u)), []);
  return live;
}

/** Polling interval to use for a query: none when Firestore pushes changes, 5s otherwise. */
export function usePollInterval(): number | false {
  return useIsLive() ? false : 5000;
}

/**
 * Firestore as a change signal: when a watched collection changes, invalidate the matching
 * TanStack queries. The API stays the single source of truth for data and shape.
 */
export function useLiveInvalidation() {
  const qc = useQueryClient();
  const live = useIsLive();
  useEffect(() => {
    if (!live) return;
    const watch: [string, string[][]][] = [
      ['disputes', [['disputes'], ['dispute']]],
      ['proposed_actions', [['dispute'], ['action']]],
      ['recon_exceptions', [['recon']]],
      ['recon_runs', [['recon']]],
    ];
    const unsubs = watch.map(([col, keys]) => {
      let first = true;
      return onSnapshot(
        query(collection(db, col), orderBy('id'), limit(500)),
        () => {
          if (first) {
            first = false; // initial snapshot: data is already being fetched
            return;
          }
          keys.forEach((queryKey) => void qc.invalidateQueries({ queryKey }));
        },
        () => undefined,
      );
    });
    return () => unsubs.forEach((u) => u());
  }, [live, qc]);
}

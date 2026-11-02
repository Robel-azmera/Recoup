import type { AuditEntry } from '@recoup/shared';
import { useQuery } from '@tanstack/react-query';
import { collection, limit, onSnapshot, orderBy, query, where } from 'firebase/firestore';
import { useEffect, useState } from 'react';

import { getAudit } from '../lib/api';
import { auth, db } from '../lib/firebase';

/**
 * Live audit feed. Prefers a Firestore listener (needs the Firebase custom-token sign-in);
 * falls back to polling GET /audit when listeners aren't available.
 */
export function useAuditFeed(subjectId?: string, max = 40) {
  const [live, setLive] = useState<AuditEntry[] | null>(null);
  const [liveFailed, setLiveFailed] = useState(false);
  const [signedIn, setSignedIn] = useState(!!auth.currentUser);

  useEffect(() => auth.onAuthStateChanged((u) => setSignedIn(!!u)), []);

  useEffect(() => {
    if (!signedIn) return;
    setLiveFailed(false);
    const base = collection(db, 'audit_log');
    const q = subjectId
      ? query(base, where('subject_id', '==', subjectId), orderBy('id', 'desc'), limit(max))
      : query(base, orderBy('id', 'desc'), limit(max));
    return onSnapshot(
      q,
      (snap) => setLive(snap.docs.map((d) => d.data() as AuditEntry)),
      () => setLiveFailed(true),
    );
  }, [signedIn, subjectId, max]);

  const useLive = signedIn && !liveFailed;
  const polled = useQuery({
    queryKey: ['audit', subjectId, max],
    queryFn: () => getAudit(subjectId, max),
    enabled: !useLive,
    refetchInterval: 4000,
  });

  return {
    entries: useLive ? live : (polled.data ?? null),
    isLoading: useLive ? live === null : polled.isLoading,
    mode: useLive ? ('live' as const) : ('polling' as const),
  };
}

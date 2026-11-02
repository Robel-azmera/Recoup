import { onAuthStateChanged, signInWithCustomToken, signOut, type User } from 'firebase/auth';
import { useEffect, useState } from 'react';

import { getToken, login as apiLogin, setToken } from '../lib/api';
import { auth } from '../lib/firebase';

export function useAuth() {
  const [token, setTok] = useState(getToken);
  const [firebaseUser, setFirebaseUser] = useState<User | null>(auth.currentUser);

  useEffect(() => {
    const sync = () => setTok(getToken());
    window.addEventListener('recoup-auth', sync);
    const unsub = onAuthStateChanged(auth, setFirebaseUser);
    return () => {
      window.removeEventListener('recoup-auth', sync);
      unsub();
    };
  }, []);

  return {
    token,
    firebaseUser,
    async login(password: string) {
      const res = await apiLogin(password);
      setToken(res.token);
      if (res.firebase_token) {
        try {
          await signInWithCustomToken(auth, res.firebase_token);
        } catch {
          /* live listeners unavailable; the feed falls back to polling */
        }
      }
    },
    async logout() {
      setToken(null);
      await signOut(auth).catch(() => undefined);
    },
  };
}

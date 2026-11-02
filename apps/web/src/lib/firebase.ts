import { initializeApp } from 'firebase/app';
import { connectAuthEmulator, getAuth } from 'firebase/auth';
import { connectFirestoreEmulator, getFirestore } from 'firebase/firestore';

const env = import.meta.env;

const app = initializeApp({
  projectId: (env.VITE_FIREBASE_PROJECT_ID as string | undefined) ?? 'demo-recoup',
  apiKey: (env.VITE_FIREBASE_API_KEY as string | undefined) ?? 'demo-key',
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  appId: env.VITE_FIREBASE_APP_ID as string | undefined,
});

export const db = getFirestore(app);
export const auth = getAuth(app);

const firestoreEmulator = env.VITE_FIRESTORE_EMULATOR_HOST as string | undefined;
if (firestoreEmulator) {
  const [host, port] = firestoreEmulator.split(':');
  connectFirestoreEmulator(db, host ?? 'localhost', Number(port ?? 8081));
}
const authEmulator = env.VITE_AUTH_EMULATOR_URL as string | undefined;
if (authEmulator) {
  connectAuthEmulator(auth, authEmulator, { disableWarnings: true });
}

import { initializeApp } from 'firebase/app';
import { connectFirestoreEmulator, getFirestore } from 'firebase/firestore';

const env = import.meta.env;

const app = initializeApp({
  projectId: (env.VITE_FIREBASE_PROJECT_ID as string | undefined) ?? 'demo-recoup',
  apiKey: (env.VITE_FIREBASE_API_KEY as string | undefined) ?? 'demo-key',
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  appId: env.VITE_FIREBASE_APP_ID as string | undefined,
});

export const db = getFirestore(app);

const emulator = env.VITE_FIRESTORE_EMULATOR_HOST as string | undefined;
if (emulator) {
  const [host, port] = emulator.split(':');
  connectFirestoreEmulator(db, host ?? 'localhost', Number(port ?? 8081));
}

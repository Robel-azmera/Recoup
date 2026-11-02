import { Loader2, Lock } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { toast } from 'sonner';

import { ThemeToggle } from '../components/ThemeToggle';
import { ApiError } from '../lib/api';

export function Login({ onLogin }: { onLogin: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await onLogin(password);
      toast.success('Signed in');
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not reach the API');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <form onSubmit={submit} className="card w-full max-w-sm space-y-5 p-6">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-lg bg-brand-600 font-bold text-white">
            R
          </div>
          <div>
            <h1 className="text-lg font-semibold">Recoup</h1>
            <p className="text-sm text-slate-500">AI payment operations analyst</p>
          </div>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="password" className="text-sm font-medium">
            Admin password
          </label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
          />
        </div>
        <button type="submit" className="btn-primary w-full" disabled={busy || !password}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Lock className="h-4 w-4" />}
          Sign in
        </button>
      </form>
    </div>
  );
}

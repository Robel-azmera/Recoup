import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { PlugZap } from 'lucide-react';

import { getHealth } from '../lib/api';
import { EmptyState } from './EmptyState';

const LABELS: Record<string, string> = {
  stripe: 'Stripe',
  gemini: 'Gemini',
  telegram: 'Telegram',
  calendar: 'Google Calendar',
  email: 'Resend email',
  firestore_emulator: 'Firestore emulator',
};

export function IntegrationStatus() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth });
  return (
    <section className="card p-4" aria-labelledby="integrations-heading">
      <h2 id="integrations-heading" className="mb-3 text-sm font-semibold">
        Integrations
      </h2>
      {health.isLoading && (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton h-9" />
          ))}
        </div>
      )}
      {health.isError && (
        <EmptyState icon={PlugZap} title="API unreachable">
          Start the stack with <code className="font-mono">pnpm dev</code> and refresh.
        </EmptyState>
      )}
      {health.data && (
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(health.data.integrations).map(([name, real]) => (
            <li
              key={name}
              className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
            >
              <span>{LABELS[name] ?? name}</span>
              <span
                className={clsx(
                  'rounded-full px-2 py-0.5 text-xs font-medium',
                  real
                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                    : 'bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
                )}
              >
                {real ? (name === 'firestore_emulator' ? 'on' : 'live') : 'mock'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

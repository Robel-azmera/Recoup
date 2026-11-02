import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { PlugZap } from 'lucide-react';

import { EmptyState } from '../components/EmptyState';
import { getHealth } from '../lib/api';

export function Overview() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="text-sm text-slate-500">KPIs and the live agent feed arrive in Phase 6.</p>
      </div>

      <section className="card p-4" aria-labelledby="integrations-heading">
        <h2 id="integrations-heading" className="mb-3 text-sm font-semibold">
          Integrations
        </h2>
        {health.isLoading && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-9" />
            ))}
          </div>
        )}
        {health.isError && (
          <EmptyState icon={PlugZap} title="API unreachable">
            Start the API with <code className="font-mono">honcho start</code> and refresh.
          </EmptyState>
        )}
        {health.data && (
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {Object.entries(health.data.integrations).map(([name, real]) => (
              <li
                key={name}
                className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
              >
                <span className="capitalize">{name.replace('_', ' ')}</span>
                <span
                  className={clsx(
                    'rounded-full px-2 py-0.5 text-xs font-medium',
                    real
                      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'
                      : 'bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
                  )}
                >
                  {real ? 'live' : 'mock'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

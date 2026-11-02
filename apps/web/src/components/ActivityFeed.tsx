import type { AuditEntry } from '@recoup/shared';
import clsx from 'clsx';
import { Activity, Bot, Cog, User } from 'lucide-react';

import { useAuditFeed } from '../hooks/useAuditFeed';
import { formatMoney } from '../lib/format';
import { EmptyState } from './EmptyState';
import { RelativeTime } from './RelativeTime';

const STEP_LABELS: Record<string, string> = {
  'webhook.received': 'Stripe event received',
  'webhook.failed': 'Stripe event failed',
  'dispute.ingested': 'New dispute ingested',
  'dispute.synced': 'Dispute synced',
  'dispute.closed': 'Dispute closed',
  'fraud_warning.received': 'Early fraud warning received',
  'demo.charge_requested': 'Demo charge requested',
  'seed.completed': 'Demo data seeded',
  'sync.completed': 'Stripe sync completed',
};

function describe(e: AuditEntry): string {
  const out = (e.output ?? {}) as Record<string, unknown>;
  const inp = (e.input ?? {}) as Record<string, unknown>;
  const parts: string[] = [];
  if (typeof inp.type === 'string') parts.push(inp.type);
  if (typeof out.category === 'string') parts.push(out.category.replaceAll('_', ' '));
  if (typeof out.amount === 'number')
    parts.push(formatMoney(out.amount, typeof out.currency === 'string' ? out.currency : 'usd'));
  if (typeof out.result === 'string') parts.push(out.result);
  if (typeof inp.scenario === 'string') parts.push(inp.scenario.replaceAll('_', ' '));
  if (typeof out.error === 'string') parts.push(out.error);
  return parts.join(' · ');
}

const ACTOR_ICON = { agent: Bot, user: User, system: Cog } as const;

export function ActivityFeed({
  subjectId,
  title = 'Agent activity',
}: {
  subjectId?: string;
  title?: string;
}) {
  const { entries, isLoading, mode } = useAuditFeed(subjectId);

  return (
    <section className="card flex min-h-0 flex-col" aria-labelledby="feed-heading">
      <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <h2 id="feed-heading" className="text-sm font-semibold">
          {title}
        </h2>
        <span
          className="flex items-center gap-1.5 text-xs text-slate-500"
          title={mode === 'live' ? 'Firestore listener' : 'Polling the API every 4s'}
        >
          <span
            className={clsx(
              'h-2 w-2 rounded-full',
              mode === 'live' ? 'animate-pulse bg-emerald-500' : 'bg-amber-500',
            )}
            aria-hidden
          />
          {mode}
        </span>
      </header>
      {isLoading ? (
        <ul className="space-y-3 p-4" aria-busy="true" aria-label="Loading activity">
          {Array.from({ length: 5 }).map((_, i) => (
            <li key={i} className="skeleton h-10" />
          ))}
        </ul>
      ) : !entries?.length ? (
        <EmptyState icon={Activity} title="No activity yet">
          Seed the demo data or create a disputed charge from Settings → Demo Console.
        </EmptyState>
      ) : (
        <ol
          className="max-h-[28rem] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800"
          aria-live="polite"
        >
          {entries.map((e) => {
            const Icon = ACTOR_ICON[e.actor] ?? Cog;
            return (
              <li key={e.id} className="flex gap-3 px-4 py-2.5">
                <Icon
                  className={clsx(
                    'mt-0.5 h-4 w-4 shrink-0',
                    e.level === 'error'
                      ? 'text-rose-500'
                      : e.level === 'warn'
                        ? 'text-amber-500'
                        : 'text-slate-400',
                  )}
                  aria-label={e.actor}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="truncate text-sm font-medium">{STEP_LABELS[e.step] ?? e.step}</p>
                    <RelativeTime value={e.ts} className="shrink-0 text-xs text-slate-500" />
                  </div>
                  <p className="truncate text-xs text-slate-500">
                    {describe(e)}
                    {e.subject_id && <span className="ml-1 font-mono">{e.subject_id}</span>}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

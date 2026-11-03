import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Loader2,
  PlayCircle,
  ShieldAlert,
  Siren,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';

import { ActivityFeed } from '../components/ActivityFeed';
import { CategoryChart } from '../components/CategoryChart';
import { EmptyState } from '../components/EmptyState';
import { Badge, Countdown, Money } from '../components/ui';
import { ApproveDialog } from '../components/workspace/ApproveDialog';
import { usePollInterval } from '../hooks/useLive';
import {
  ApiError,
  dismissFraudWarning,
  getKpis,
  listFraudWarnings,
  runDailyJob,
  runRecon,
} from '../lib/api';
import { formatMoney, formatPercent } from '../lib/format';
import { CATEGORY_LABEL } from '../lib/labels';

function Stat({
  label,
  value,
  sub,
  loading,
  children,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  loading?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      {loading ? (
        <div className="skeleton mt-2 h-8 w-28" />
      ) : (
        <div className="tabular mt-1 text-2xl font-semibold">{value}</div>
      )}
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
      {children}
    </div>
  );
}

/** Dispute rate vs the network monitoring threshold: a meter + status icon and label (never colour alone). */
function RateMeter({ rate, threshold }: { rate: number; threshold: number }) {
  const over = rate > threshold;
  const scale = Math.max(threshold * 2, rate * 1.1);
  return (
    <div className="mt-2">
      <div className="relative h-2 rounded-full bg-slate-100 dark:bg-slate-800" aria-hidden>
        <div
          className={clsx('h-2 rounded-full', over ? 'bg-rose-500' : 'bg-emerald-500')}
          style={{ width: `${Math.min(100, (rate / scale) * 100)}%` }}
        />
        <div
          className="absolute -top-1 h-4 w-0.5 bg-slate-500"
          style={{ left: `${(threshold / scale) * 100}%` }}
          title="Threshold"
        />
      </div>
      <div
        className={clsx(
          'mt-1 flex items-center gap-1 text-xs font-medium',
          over ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-700 dark:text-emerald-400',
        )}
      >
        {over ? (
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
        )}
        {over ? 'Above' : 'Below'} the {formatPercent(threshold)} monitoring threshold
      </div>
    </div>
  );
}

function FraudWarnings() {
  const qc = useQueryClient();
  const refetchInterval = usePollInterval();
  const q = useQuery({
    queryKey: ['efw'],
    queryFn: listFraudWarnings,
    refetchInterval: refetchInterval || 8000,
  });
  const [approveId, setApproveId] = useState<string | null>(null);
  const dismiss = useMutation({
    mutationFn: dismissFraudWarning,
    onSuccess: () => {
      toast.success('Fraud warning dismissed');
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Dismiss failed'),
  });
  const open = (q.data ?? []).filter((e) => e.status === 'open' || e.status === 'refund_proposed');
  const done = (q.data ?? [])
    .filter((e) => e.status === 'refunded' || e.status === 'dismissed')
    .slice(0, 3);

  return (
    <section className="card" aria-labelledby="efw-heading">
      <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <Siren className="h-4 w-4 text-rose-500" aria-hidden />
        <h2 id="efw-heading" className="text-sm font-semibold">
          Early fraud warnings
        </h2>
        {open.length > 0 && <Badge tone="danger">{open.length} need action</Badge>}
      </header>
      {q.isLoading ? (
        <div className="space-y-2 p-4">
          <div className="skeleton h-16" />
        </div>
      ) : !open.length && !done.length ? (
        <EmptyState icon={Siren} title="No fraud warnings">
          Issuer warnings arrive here; the agent recommends a refund before they become disputes.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {open.map((e) => (
            <li key={e.id} className="space-y-2 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Money minor={e.amount} currency={e.currency} className="font-semibold" />
                <Badge tone="danger">{e.fraud_type.replaceAll('_', ' ')}</Badge>
                <span className="font-mono text-xs text-slate-500">
                  {e.order_id ?? e.charge_id}
                </span>
              </div>
              <p className="text-sm text-slate-600 dark:text-slate-300">{e.recommendation}</p>
              <div className="flex flex-wrap gap-2">
                {e.proposed_action_id && (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => setApproveId(e.proposed_action_id!)}
                  >
                    Approve refund
                  </button>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={dismiss.isPending}
                  onClick={() => dismiss.mutate(e.id)}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
          {done.map((e) => (
            <li key={e.id} className="flex items-center gap-2 px-4 py-2 text-sm text-slate-500">
              <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden />
              <Money minor={e.amount} currency={e.currency} /> {e.status}
              <span className="font-mono text-xs">{e.order_id}</span>
            </li>
          ))}
        </ul>
      )}
      <ApproveDialog actionId={approveId} onClose={() => setApproveId(null)} />
    </section>
  );
}

export function Overview() {
  const qc = useQueryClient();
  const refetchInterval = usePollInterval();
  const kpis = useQuery({
    queryKey: ['kpis'],
    queryFn: getKpis,
    refetchInterval: refetchInterval || 10000,
  });
  const k = kpis.data;
  const loading = kpis.isLoading;
  const daily = useMutation({
    mutationFn: runDailyJob,
    onSuccess: (s) => {
      toast.success('Daily job complete', {
        description: `Recon ${s.recon_new_exceptions} new · ${s.deadline_alerts} deadline alert(s) · brief via ${s.brief_channels.join(' + ') || 'no channel'}`,
      });
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Daily job failed'),
  });
  const recon = useMutation({
    mutationFn: runRecon,
    onSuccess: (r) => {
      toast.success('Reconciliation complete', {
        description: `${r.exception_count} exception(s), ${r.new_exceptions} new`,
      });
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Reconciliation failed'),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="text-sm text-slate-500">
            Money at stake, what's due, and what the agent is doing.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => recon.mutate()}
            disabled={recon.isPending}
          >
            {recon.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <PlayCircle className="h-4 w-4" />
            )}
            Run reconciliation now
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => daily.mutate()}
            disabled={daily.isPending}
          >
            {daily.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <PlayCircle className="h-4 w-4" />
            )}
            Run daily job + brief
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="$ at risk"
          loading={loading}
          value={formatMoney(k?.at_risk ?? 0)}
          sub={k && `${k.open_disputes} open · ${k.due_72h} due within 72h`}
        />
        <Stat
          label="$ recovered"
          loading={loading}
          value={formatMoney(k?.recovered ?? 0)}
          sub={k && `${k.won} won · ${k.lost} lost · ${k.accepted} accepted`}
        />
        <Stat
          label="Win rate"
          loading={loading}
          value={k?.win_rate == null ? '–' : formatPercent(k.win_rate, 0)}
          sub={
            k &&
            (k.won + k.lost ? `of ${k.won + k.lost} decided disputes` : 'No decided disputes yet')
          }
        />
        <Stat
          label="Dispute rate (30d)"
          loading={loading}
          value={k ? formatPercent(k.dispute_rate, 2) : '–'}
          sub={k && `${k.disputes_30d} disputes / ${k.charges_30d} charges`}
        >
          {k && <RateMeter rate={k.dispute_rate} threshold={k.dispute_rate_threshold} />}
        </Stat>
      </div>

      <div className="grid gap-6 lg:grid-cols-5">
        <section className="card p-4 lg:col-span-3" aria-labelledby="cat-heading">
          <h2 id="cat-heading" className="mb-3 text-sm font-semibold">
            Disputed $ by reason
          </h2>
          {loading ? (
            <div className="skeleton h-40" />
          ) : (
            <CategoryChart data={k?.by_category ?? []} />
          )}
        </section>
        <section className="card lg:col-span-2" aria-labelledby="deadline-heading">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
            <h2 id="deadline-heading" className="flex items-center gap-2 text-sm font-semibold">
              <ShieldAlert className="h-4 w-4 text-brand-600" aria-hidden /> Next deadlines
            </h2>
            <Link to="/disputes" className="text-xs font-medium text-brand-600 hover:underline">
              All disputes
            </Link>
          </header>
          {loading ? (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="skeleton h-10" />
              ))}
            </div>
          ) : !k?.deadlines.length ? (
            <EmptyState icon={CheckCircle2} title="Nothing due">
              No open disputes. New ones appear the moment Stripe sends the webhook.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {k.deadlines.map((d) => (
                <li key={d.dispute_id}>
                  <Link
                    to={`/disputes/${d.dispute_id}`}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/40"
                  >
                    <div className="min-w-0 flex-1">
                      <Money
                        minor={d.amount}
                        currency={d.currency}
                        className="text-sm font-medium"
                      />
                      <div className="truncate text-xs text-slate-500">
                        {CATEGORY_LABEL[d.category as keyof typeof CATEGORY_LABEL] ?? d.category} ·
                        score {d.win_score ?? '–'}
                      </div>
                    </div>
                    <Countdown due={d.due_by} />
                    <ChevronRight className="h-4 w-4 text-slate-400" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <FraudWarnings />
          <section className="card p-4" aria-labelledby="recon-heading">
            <div className="flex items-center justify-between">
              <h2 id="recon-heading" className="text-sm font-semibold">
                Reconciliation
              </h2>
              <Link
                to="/reconciliation"
                className="text-xs font-medium text-brand-600 hover:underline"
              >
                Open
              </Link>
            </div>
            {loading ? (
              <div className="skeleton mt-2 h-6 w-48" />
            ) : (
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
                <b className="tabular">{k?.recon_open ?? 0}</b> open exception(s),{' '}
                <b className="tabular">{formatMoney(k?.recon_discrepancy ?? 0)}</b> unexplained.
              </p>
            )}
          </section>
        </div>
        <ActivityFeed />
      </div>
    </div>
  );
}

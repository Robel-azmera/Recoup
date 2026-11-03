import type { ReconExceptionType } from '@recoup/shared';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import {
  ArrowRightLeft,
  CheckCheck,
  ChevronDown,
  Clock,
  Copy,
  Lightbulb,
  Loader2,
  PlayCircle,
  Receipt,
  RotateCcw,
  Scale,
  SearchX,
  Timer,
  Undo2,
  type LucideIcon,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';

import { EmptyState } from '../components/EmptyState';
import { RelativeTime } from '../components/RelativeTime';
import { Badge, Money } from '../components/ui';
import { usePollInterval } from '../hooks/useLive';
import {
  ApiError,
  getPayoutViews,
  getReconSummary,
  runRecon,
  updateReconException,
  type PayoutView,
  type ReconAction,
  type ReconException,
} from '../lib/api';
import { formatAbsolute, formatMoney } from '../lib/format';

const TYPE_META: Record<ReconExceptionType, { label: string; icon: LucideIcon }> = {
  missing_settlement: { label: 'Missing settlement', icon: SearchX },
  amount_mismatch: { label: 'Amount mismatch', icon: Scale },
  unexpected_fee: { label: 'Unexpected fee', icon: Receipt },
  orphan_refund: { label: 'Refund without order', icon: Undo2 },
  delayed_payout: { label: 'Delayed payout', icon: Timer },
  duplicate_charge: { label: 'Duplicate charge', icon: Copy },
};

type Tab = 'open' | 'snoozed' | 'expected' | 'resolved' | 'all';
const TABS: Tab[] = ['open', 'snoozed', 'expected', 'resolved', 'all'];

function Tile({
  label,
  value,
  sub,
  loading,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      {loading ? (
        <div className="skeleton mt-2 h-7 w-20" />
      ) : (
        <div className="tabular mt-1 text-2xl font-semibold">{value}</div>
      )}
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

function ExceptionCard({ ex }: { ex: ReconException }) {
  const qc = useQueryClient();
  const meta = TYPE_META[ex.type];
  const update = useMutation({
    mutationFn: (action: ReconAction) => updateReconException(ex.id, action),
    onSuccess: (r, action) => {
      toast.success(
        {
          resolve: 'Resolved',
          snooze: 'Snoozed for 3 days',
          expected: 'Marked as expected',
          reopen: 'Reopened',
        }[action],
        { description: TYPE_META[r.type].label },
      );
      void qc.invalidateQueries({ queryKey: ['recon'] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Update failed'),
  });
  const busy = update.isPending;
  const closed = ex.status !== 'open';

  return (
    <li className={clsx('card p-4', closed && 'opacity-80')}>
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
          <meta.icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{meta.label}</h3>
            <Money minor={Math.abs(ex.amount)} currency={ex.currency} className="font-semibold" />
            {ex.status !== 'open' && (
              <Badge
                tone={
                  ex.status === 'resolved'
                    ? 'success'
                    : ex.status === 'expected'
                      ? 'neutral'
                      : 'info'
                }
              >
                {ex.status}
                {ex.status === 'snoozed' && ex.snoozed_until && (
                  <> until {formatAbsolute(ex.snoozed_until)}</>
                )}
              </Badge>
            )}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {ex.refs.map((r) => (
              <code
                key={r}
                className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              >
                {r}
              </code>
            ))}
          </div>
        </div>
        <span className="text-xs text-slate-500">
          Found <RelativeTime value={ex.detected_at} />
        </span>
      </div>

      <p className="mt-3 text-sm leading-relaxed text-slate-700 dark:text-slate-300">
        {ex.explanation ?? ex.detail}
      </p>
      {ex.suggested_action && (
        <p className="mt-2 flex gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-700 dark:bg-brand-500/10 dark:text-brand-100">
          <Lightbulb className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span>
            <span className="font-medium">Suggested: </span>
            {ex.suggested_action}
          </span>
        </p>
      )}
      {ex.resolution && (
        <p className="mt-2 text-xs text-slate-500">
          Resolution: {ex.resolution}
          {ex.resolved_by && ` (${ex.resolved_by})`}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {ex.status === 'open' ? (
          <>
            <button
              type="button"
              className="btn-primary"
              disabled={busy}
              onClick={() => update.mutate('resolve')}
            >
              <CheckCheck className="h-4 w-4" /> Resolve
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => update.mutate('snooze')}
            >
              <Clock className="h-4 w-4" /> Snooze 3d
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => update.mutate('expected')}
            >
              Mark expected
            </button>
          </>
        ) : (
          <button
            type="button"
            className="btn-secondary"
            disabled={busy}
            onClick={() => update.mutate('reopen')}
          >
            <RotateCcw className="h-4 w-4" /> Reopen
          </button>
        )}
        {busy && <Loader2 className="h-4 w-4 animate-spin text-slate-400" aria-label="Saving" />}
        <span className="ml-auto text-[11px] text-slate-400">
          explained by {ex.explained_by ?? '—'}
        </span>
      </div>
    </li>
  );
}

function PayoutRow({ v }: { v: PayoutView }) {
  const [open, setOpen] = useState(false);
  const p = v.payout;
  const flagged = v.exception_ids.length > 0 || v.lines.some((l) => l.exception_ids.length > 0);
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-800/40"
      >
        <ChevronDown
          className={clsx('h-4 w-4 shrink-0 text-slate-400 transition', open && 'rotate-180')}
          aria-hidden
        />
        <span className="font-mono text-xs">{p.id}</span>
        <Money minor={p.amount} currency={p.currency} className="font-semibold" />
        <Badge tone={p.status === 'paid' ? 'success' : v.exception_ids.length ? 'danger' : 'info'}>
          {p.status.replace('_', ' ')}
        </Badge>
        <span className="text-slate-500" title={formatAbsolute(p.arrival_date)}>
          arrives <RelativeTime value={p.arrival_date} />
        </span>
        <span className="ml-auto text-xs text-slate-500">
          {v.lines.length} txns
          {flagged && (
            <Badge tone="warning" className="ml-2">
              has exceptions
            </Badge>
          )}
        </span>
      </button>
      {open && (
        <div className="overflow-x-auto border-t border-slate-100 bg-slate-50/50 dark:border-slate-800 dark:bg-slate-900/40">
          <table className="w-full min-w-[36rem] text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">Transaction</th>
                <th className="px-2 py-2 font-medium">Type</th>
                <th className="px-2 py-2 text-right font-medium">Amount</th>
                <th className="px-2 py-2 text-right font-medium">Fee</th>
                <th className="px-2 py-2 text-right font-medium">Net</th>
                <th className="px-4 py-2 font-medium">Order</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {v.lines.map((l) => (
                <tr
                  key={l.txn.id}
                  className={clsx(l.exception_ids.length && 'bg-amber-50 dark:bg-amber-500/5')}
                >
                  <td className="px-4 py-1.5 font-mono">{l.txn.id}</td>
                  <td className="px-2 py-1.5">{l.txn.type}</td>
                  <td className="tabular px-2 py-1.5 text-right">
                    {formatMoney(l.txn.amount, l.txn.currency)}
                  </td>
                  <td className="tabular px-2 py-1.5 text-right">
                    {formatMoney(l.txn.fee, l.txn.currency)}
                  </td>
                  <td className="tabular px-2 py-1.5 text-right">
                    {formatMoney(l.txn.net, l.txn.currency)}
                  </td>
                  <td className="px-4 py-1.5">
                    {l.order_id ? (
                      <span className="font-mono">{l.order_id}</span>
                    ) : (
                      <span className="text-rose-600">no order</span>
                    )}
                    {l.exception_ids.length > 0 && (
                      <Badge tone="warning" className="ml-2">
                        exception
                      </Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="font-medium">
                <td className="px-4 py-2" colSpan={4}>
                  Net of transactions
                </td>
                <td
                  className={clsx(
                    'tabular px-2 py-2 text-right',
                    v.net_total !== p.amount && 'text-rose-600',
                  )}
                >
                  {formatMoney(v.net_total, p.currency)}
                </td>
                <td className="px-4 py-2 text-slate-500">
                  payout {formatMoney(p.amount, p.currency)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </li>
  );
}

export function Reconciliation() {
  const qc = useQueryClient();
  const refetchInterval = usePollInterval();
  const [tab, setTab] = useState<Tab>('open');
  const summary = useQuery({
    queryKey: ['recon', 'summary', tab],
    queryFn: () => getReconSummary(tab),
    refetchInterval,
  });
  const payouts = useQuery({ queryKey: ['recon', 'payouts'], queryFn: getPayoutViews });
  const run = useMutation({
    mutationFn: runRecon,
    onSuccess: (r) => {
      toast.success('Reconciliation complete', {
        description: `${r.matched} matched · ${r.exception_count} exception(s) · ${r.new_exceptions} new`,
      });
      void qc.invalidateQueries({ queryKey: ['recon'] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Reconciliation failed'),
  });

  const r = summary.data?.run;
  const loading = summary.isLoading;
  const items = summary.data?.exceptions ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Reconciliation</h1>
          <p className="text-sm text-slate-500">
            {r ? (
              <>
                Last run <RelativeTime value={r.started_at} /> · runs daily at 07:00 EAT
              </>
            ) : (
              'Payouts matched against the order ledger. Runs daily at 07:00 EAT.'
            )}
          </p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => run.mutate()}
          disabled={run.isPending}
        >
          {run.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <PlayCircle className="h-4 w-4" />
          )}
          Run reconciliation now
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Open discrepancy"
          loading={loading}
          value={formatMoney(r?.open_discrepancy ?? 0)}
          sub={r ? `${items.length} in view` : 'No run yet'}
        />
        <Tile
          label="Exceptions"
          loading={loading}
          value={r?.exception_count ?? '–'}
          sub={r ? `${r.new_exceptions} new · ${r.auto_resolved} auto-resolved` : undefined}
        />
        <Tile
          label="Matched orders"
          loading={loading}
          value={r?.matched ?? '–'}
          sub={r ? `${r.payouts_checked} payouts checked` : undefined}
        />
        <Tile
          label="Settled (paid payouts)"
          loading={loading}
          value={r ? formatMoney(r.total_settled) : '–'}
          sub={r?.status === 'failed' ? `Failed: ${r.error}` : undefined}
        />
      </div>

      <section aria-labelledby="ex-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="ex-heading" className="text-sm font-semibold">
            Exceptions
          </h2>
          <div
            role="tablist"
            aria-label="Exception status"
            className="inline-flex flex-wrap rounded-lg border border-slate-200 bg-white p-0.5 dark:border-slate-800 dark:bg-slate-900"
          >
            {TABS.map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={clsx(
                  'rounded-md px-3 py-1.5 text-sm font-medium capitalize transition',
                  tab === t
                    ? 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-white'
                    : 'text-slate-500',
                )}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        {loading ? (
          <div className="space-y-3" aria-busy="true">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="skeleton h-40" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="card">
            <EmptyState
              icon={r ? CheckCheck : ArrowRightLeft}
              title={
                r
                  ? tab === 'open'
                    ? 'All reconciled'
                    : `No ${tab} exceptions`
                  : 'No reconciliation run yet'
              }
              action={
                !r && (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => run.mutate()}
                    disabled={run.isPending}
                  >
                    Run reconciliation now
                  </button>
                )
              }
            >
              {r
                ? 'Every payout in the period matches the order ledger.'
                : 'Match Stripe payouts and balance transactions against the order ledger.'}
            </EmptyState>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((ex) => (
              <ExceptionCard key={ex.id} ex={ex} />
            ))}
          </ul>
        )}
      </section>

      <section className="card overflow-hidden" aria-labelledby="payouts-heading">
        <header className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          <h2 id="payouts-heading" className="text-sm font-semibold">
            Payout → order match
          </h2>
          <p className="text-xs text-slate-500">
            Expand a payout to see each transaction and the order it settles.
          </p>
        </header>
        {payouts.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="skeleton h-10" />
            ))}
          </div>
        ) : !payouts.data?.length ? (
          <EmptyState icon={ArrowRightLeft} title="No payouts synced yet">
            Seed the demo data or connect Stripe, then{' '}
            <Link to="/settings" className="text-brand-600 underline">
              sync
            </Link>
            .
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {payouts.data.map((v) => (
              <PayoutRow key={v.payout.id} v={v} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

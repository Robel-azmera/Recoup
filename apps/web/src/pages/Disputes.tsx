import type { Dispute } from '@recoup/shared';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { ChevronRight, Loader2, Search, ShieldCheck } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { EmptyState } from '../components/EmptyState';
import { Badge, Countdown, Dialog, Money, StatusBadge, scoreTone } from '../components/ui';
import { usePollInterval } from '../hooks/useLive';
import { approveAction, getAction, listDisputes, proposeForDispute } from '../lib/api';
import { formatMoney } from '../lib/format';
import { CATEGORY_LABEL, OPEN_STATUSES } from '../lib/labels';

type View = 'open' | 'closed' | 'all';

const isOpen = (d: Dispute) => OPEN_STATUSES.includes(d.status);

function Score({ d }: { d: Dispute }) {
  if (d.win_score == null)
    return <span className="text-sm text-slate-400">{d.status === 'drafting' ? '…' : '–'}</span>;
  return <Badge tone={scoreTone(d.win_score)}>{d.win_score}</Badge>;
}

function Recommendation({ d }: { d: Dispute }) {
  if (!d.recommendation || !isOpen(d)) return null;
  return (
    <span
      className={clsx(
        'text-xs font-medium',
        d.recommendation === 'fight' ? 'text-brand-600 dark:text-brand-100' : 'text-slate-500',
      )}
    >
      {d.recommendation === 'fight' ? 'Fight' : 'Accept'}
    </span>
  );
}

async function acceptOne(d: Dispute) {
  const action = await proposeForDispute(d.id, 'accept');
  const view = await getAction(action.id);
  if (!view.approval_token) throw new Error(`${d.id}: action is ${view.action.status}`);
  const done = await approveAction(action.id, view.approval_token);
  if (done.status !== 'executed') throw new Error(`${d.id}: ${done.error ?? done.status}`);
}

export function Disputes() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const refetchInterval = usePollInterval();
  const disputes = useQuery({ queryKey: ['disputes'], queryFn: listDisputes, refetchInterval });
  const [view, setView] = useState<View>('open');
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (disputes.data ?? []).filter(
      (d) =>
        (view === 'all' || (view === 'open') === isOpen(d)) &&
        (category === 'all' || d.category === category) &&
        (!q || [d.id, d.order_id, d.customer_email].some((v) => v?.toLowerCase().includes(q))),
    );
  }, [disputes.data, view, category, search]);

  const open = (disputes.data ?? []).filter(isOpen);
  const atRisk = open.reduce((s, d) => s + d.amount, 0);
  const bulkCandidates = rows.filter((d) => isOpen(d) && d.recommendation === 'accept');
  const selectedRows = rows.filter((d) => selected.has(d.id));

  const bulk = useMutation({
    mutationFn: async (items: Dispute[]) => {
      const results = await Promise.allSettled(items.map(acceptOne));
      return results;
    },
    onSuccess: (results) => {
      const failed = results.filter((r) => r.status === 'rejected');
      if (failed.length)
        toast.error(`${failed.length} of ${results.length} could not be accepted`, {
          description: failed.map((f) => (f as PromiseRejectedResult).reason?.message).join('\n'),
        });
      else toast.success(`Accepted ${results.length} dispute(s)`);
      setSelected(new Set());
      setConfirmBulk(false);
      void qc.invalidateQueries({ queryKey: ['disputes'] });
    },
  });

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Disputes</h1>
          <p className="text-sm text-slate-500">
            {disputes.isLoading
              ? 'Loading…'
              : `${open.length} open · ${formatMoney(atRisk)} at risk · soonest deadline first`}
          </p>
        </div>
        {selectedRows.length > 0 && (
          <button type="button" className="btn-secondary" onClick={() => setConfirmBulk(true)}>
            Accept {selectedRows.length} selected
          </button>
        )}
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div
          role="tablist"
          aria-label="Filter by state"
          className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 dark:border-slate-800 dark:bg-slate-900"
        >
          {(['open', 'closed', 'all'] as View[]).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={clsx(
                'rounded-md px-3 py-1.5 text-sm font-medium capitalize transition',
                view === v
                  ? 'bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-white'
                  : 'text-slate-500',
              )}
            >
              {v}
            </button>
          ))}
        </div>
        <label className="sr-only" htmlFor="cat">
          Category
        </label>
        <select
          id="cat"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        >
          <option value="all">All categories</option>
          {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <div className="relative sm:ml-auto sm:w-64">
          <Search
            className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400"
            aria-hidden
          />
          <input
            type="search"
            placeholder="Dispute, order or email"
            aria-label="Search disputes"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm dark:border-slate-700 dark:bg-slate-900"
          />
        </div>
      </div>

      <div className="card overflow-hidden">
        {disputes.isLoading ? (
          <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading disputes">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton h-12" />
            ))}
          </div>
        ) : disputes.isError ? (
          <EmptyState icon={ShieldCheck} title="Could not load disputes">
            {(disputes.error as Error).message}
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title={view === 'open' ? 'No open disputes' : 'Nothing here'}
          >
            {view === 'open'
              ? 'New disputes appear here the moment Stripe sends the webhook.'
              : 'Try a different filter.'}
          </EmptyState>
        ) : (
          <>
            {/* Desktop table */}
            <table className="hidden w-full text-sm md:table">
              <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-900/60">
                <tr>
                  <th className="w-10 px-4 py-2.5">
                    <span className="sr-only">Select</span>
                  </th>
                  <th className="px-2 py-2.5 font-medium">Due in</th>
                  <th className="px-2 py-2.5 font-medium">Amount</th>
                  <th className="px-2 py-2.5 font-medium">Reason</th>
                  <th className="px-2 py-2.5 font-medium">Win score</th>
                  <th className="px-2 py-2.5 font-medium">Status</th>
                  <th className="px-2 py-2.5 font-medium">Customer</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {rows.map((d) => (
                  <tr
                    key={d.id}
                    className="cursor-pointer transition hover:bg-slate-50 dark:hover:bg-slate-800/40"
                    onClick={() => navigate(`/disputes/${d.id}`)}
                  >
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      {isOpen(d) && d.recommendation === 'accept' && (
                        <input
                          type="checkbox"
                          aria-label={`Select ${d.id} for bulk accept`}
                          checked={selected.has(d.id)}
                          onChange={() => toggle(d.id)}
                          className="h-4 w-4 rounded border-slate-300"
                        />
                      )}
                    </td>
                    <td className="px-2 py-3">
                      <Countdown due={d.due_by} done={!isOpen(d)} />
                    </td>
                    <td className="px-2 py-3 font-medium">
                      <Money minor={d.amount} currency={d.currency} />
                    </td>
                    <td className="px-2 py-3">{CATEGORY_LABEL[d.category]}</td>
                    <td className="px-2 py-3">
                      <div className="flex items-center gap-2">
                        <Score d={d} />
                        <Recommendation d={d} />
                      </div>
                    </td>
                    <td className="px-2 py-3">
                      <StatusBadge status={d.status} />
                    </td>
                    <td className="max-w-[14rem] truncate px-2 py-3 text-slate-500">
                      {d.customer_email ?? '—'}
                    </td>
                    <td className="pr-3">
                      <Link
                        to={`/disputes/${d.id}`}
                        aria-label={`Open dispute ${d.id}`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ChevronRight className="h-4 w-4 text-slate-400" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Mobile cards */}
            <ul className="divide-y divide-slate-100 md:hidden dark:divide-slate-800">
              {rows.map((d) => (
                <li key={d.id}>
                  <Link to={`/disputes/${d.id}`} className="flex items-center gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <Money minor={d.amount} currency={d.currency} className="font-semibold" />
                        <Countdown due={d.due_by} done={!isOpen(d)} />
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span>{CATEGORY_LABEL[d.category]}</span>
                        <Score d={d} />
                        <StatusBadge status={d.status} />
                      </div>
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      {bulkCandidates.length > 0 && selectedRows.length === 0 && (
        <p className="text-xs text-slate-500">
          Tip: tick disputes the agent recommends accepting to close them in bulk.
        </p>
      )}

      <Dialog
        open={confirmBulk}
        onClose={() => setConfirmBulk(false)}
        title="Accept selected disputes?"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setConfirmBulk(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-danger"
              disabled={bulk.isPending}
              onClick={() => bulk.mutate(selectedRows)}
            >
              {bulk.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Accept {selectedRows.length} ·{' '}
              {formatMoney(selectedRows.reduce((s, d) => s + d.amount, 0))}
            </button>
          </>
        }
      >
        <p className="text-sm text-slate-600 dark:text-slate-300">
          Each dispute is closed in Stripe as lost. Every one still goes through the policy gate
          with your approval. This cannot be undone.
        </p>
        <ul className="mt-3 space-y-1 text-sm">
          {selectedRows.map((d) => (
            <li key={d.id} className="flex justify-between">
              <span className="font-mono text-xs">{d.id}</span>
              <Money minor={d.amount} currency={d.currency} />
            </li>
          ))}
        </ul>
      </Dialog>
    </div>
  );
}

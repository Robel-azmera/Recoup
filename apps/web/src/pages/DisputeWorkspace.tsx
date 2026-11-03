import type { ChecklistItem } from '@recoup/shared';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import {
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Pencil,
  RefreshCw,
  Send,
  ShieldX,
  XCircle,
} from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { toast } from 'sonner';

import { EmptyState } from '../components/EmptyState';
import { AgentWorking } from '../components/workspace/AgentWorking';
import { ApproveDialog } from '../components/workspace/ApproveDialog';
import { EvidenceEditor } from '../components/workspace/Evidence';
import { Timeline } from '../components/workspace/Timeline';
import { Badge, Countdown, Money, ScoreRing, StatusBadge } from '../components/ui';
import { usePollInterval } from '../hooks/useLive';
import { ApiError, editEvidence, getDispute, proposeForDispute, regenerateDraft } from '../lib/api';
import { formatAbsolute } from '../lib/format';
import { CATEGORY_LABEL, OPEN_STATUSES } from '../lib/labels';

function ChecklistView({ items }: { items: ChecklistItem[] }) {
  const Icon = { found: CheckCircle2, partial: CircleDashed, missing: XCircle };
  const color = {
    found: 'text-emerald-500',
    partial: 'text-amber-500',
    missing: 'text-slate-300 dark:text-slate-600',
  };
  return (
    <ul className="space-y-2">
      {items.map((i) => {
        const I = Icon[i.status];
        return (
          <li key={i.key} className="flex items-start gap-2 text-sm">
            <I className={clsx('mt-0.5 h-4 w-4 shrink-0', color[i.status])} aria-label={i.status} />
            <div className="min-w-0">
              <span className={clsx(i.status === 'missing' && 'text-slate-500')}>{i.label}</span>
              {i.required && i.status === 'missing' && (
                <Badge tone="danger" className="ml-2">
                  required
                </Badge>
              )}
              {i.note && <span className="block text-xs text-slate-500">{i.note}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function SkeletonWorkspace() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading dispute">
      <div className="skeleton h-20" />
      <div className="grid gap-4 lg:grid-cols-5">
        <div className="skeleton h-96 lg:col-span-2" />
        <div className="space-y-4 lg:col-span-3">
          <div className="skeleton h-32" />
          <div className="skeleton h-64" />
        </div>
      </div>
    </div>
  );
}

export function DisputeWorkspace() {
  const { id = '' } = useParams();
  const qc = useQueryClient();
  const refetchInterval = usePollInterval();
  const detail = useQuery({
    queryKey: ['dispute', id],
    queryFn: () => getDispute(id),
    refetchInterval: (q) => (q.state.data?.dispute.status === 'drafting' ? 1500 : refetchInterval),
  });
  const [instruction, setInstruction] = useState('');
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [approveId, setApproveId] = useState<string | null>(null);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['dispute', id] });
    void qc.invalidateQueries({ queryKey: ['disputes'] });
  };
  const err = (e: unknown) =>
    toast.error(e instanceof ApiError ? e.message : 'Something went wrong');

  const regenerate = useMutation({
    mutationFn: () => regenerateDraft(id, instruction.trim() || undefined),
    onSuccess: () => {
      toast.message('Agent is redrafting', {
        description: instruction || 'Fresh draft from the latest records',
      });
      setInstruction('');
      setEditing(false);
      setEdits({});
      invalidate();
    },
    onError: err,
  });
  const save = useMutation({
    mutationFn: () => editEvidence(id, edits),
    onSuccess: () => {
      toast.success('Edits saved', {
        description: 'The submit action was re-proposed with your text.',
      });
      setEditing(false);
      setEdits({});
      invalidate();
    },
    onError: err,
  });
  const propose = useMutation({
    mutationFn: (type: 'submit' | 'accept') => proposeForDispute(id, type),
    onSuccess: (a) => {
      invalidate();
      setApproveId(a.id);
    },
    onError: err,
  });

  if (detail.isLoading) return <SkeletonWorkspace />;
  if (detail.isError || !detail.data)
    return (
      <div className="card">
        <EmptyState
          icon={ShieldX}
          title="Dispute not found"
          action={
            <Link to="/disputes" className="btn-secondary">
              Back to disputes
            </Link>
          }
        >
          {(detail.error as Error | null)?.message}
        </EmptyState>
      </div>
    );

  const { dispute: d, actions } = detail.data;
  const open = OPEN_STATUSES.includes(d.status);
  const drafting = d.status === 'drafting';
  const draft = d.evidence_draft;
  const current = actions.find((a) => a.id === d.proposed_action_id && a.status === 'proposed');
  const unsupported = draft?.unsupported_sentence_ids.length ?? 0;
  const dirty = Object.keys(edits).length > 0;

  function onApprove() {
    if (dirty) return toast.error('Save or discard your edits first');
    if (current?.type === 'submit_dispute_evidence') setApproveId(current.id);
    else propose.mutate('submit');
  }
  function onAccept() {
    if (current?.type === 'accept_dispute') setApproveId(current.id);
    else propose.mutate('accept');
  }
  function onRegenerate(e: FormEvent) {
    e.preventDefault();
    regenerate.mutate();
  }

  return (
    <div className="space-y-4 pb-24 lg:pb-0">
      <Link
        to="/disputes"
        className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
      >
        <ArrowLeft className="h-4 w-4" /> Disputes
      </Link>

      <header className="card flex flex-wrap items-center gap-4 p-4">
        <ScoreRing score={d.win_score} size={64} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold">
              <Money minor={d.amount} currency={d.currency} />
            </h1>
            <Badge>{CATEGORY_LABEL[d.category]}</Badge>
            <StatusBadge status={d.status} />
            {d.recommendation && open && (
              <Badge tone={d.recommendation === 'fight' ? 'brand' : 'neutral'}>
                Agent: {d.recommendation === 'fight' ? 'fight' : 'accept'}
              </Badge>
            )}
          </div>
          <p className="mt-1 truncate text-sm text-slate-500">
            <span className="font-mono">{d.id}</span> · reason{' '}
            <span className="font-mono">{d.reason_code}</span>
            {d.order_id && (
              <>
                {' '}
                · order <span className="font-mono">{d.order_id}</span>
              </>
            )}
          </p>
        </div>
        <div className="w-full border-t border-slate-200 pt-3 sm:w-auto sm:border-0 sm:pt-0 sm:text-right dark:border-slate-800">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            {open ? 'Respond within' : 'Deadline'}
          </div>
          <Countdown due={d.due_by} done={!open} />
          <div className="text-xs text-slate-500">{formatAbsolute(d.due_by)}</div>
        </div>
      </header>

      <AgentWorking disputeId={d.id} active={drafting || regenerate.isPending} />

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-2">
          <Timeline detail={detail.data} />
        </div>

        <div className="space-y-4 lg:col-span-3">
          <div className="grid gap-4 md:grid-cols-2">
            <section className="card p-4" aria-labelledby="score-heading">
              <h2 id="score-heading" className="mb-2 text-sm font-semibold">
                Why {d.win_score ?? '–'}/100
              </h2>
              {d.win_rationale.length ? (
                <ul className="list-disc space-y-1 pl-4 text-sm text-slate-600 dark:text-slate-300">
                  {d.win_rationale.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-slate-500">No analysis yet.</p>
              )}
              {d.baseline_score != null && (
                <p className="mt-2 text-xs text-slate-500">
                  Evidence-coverage baseline: {d.baseline_score}. The model's score stays within ±20
                  of it.
                </p>
              )}
            </section>
            <section className="card p-4" aria-labelledby="checklist-heading">
              <h2 id="checklist-heading" className="mb-2 text-sm font-semibold">
                Evidence checklist
              </h2>
              {d.checklist.length ? (
                <ChecklistView items={d.checklist} />
              ) : (
                <p className="text-sm text-slate-500">Building…</p>
              )}
            </section>
          </div>

          <section className="card" aria-labelledby="evidence-heading">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <div>
                <h2 id="evidence-heading" className="text-sm font-semibold">
                  Evidence for Stripe
                </h2>
                {draft && (
                  <p className="text-xs text-slate-500">
                    {draft.model}
                    {draft.cached && ' · cached'}
                    {draft.instruction && <> · “{draft.instruction}”</>}
                    {draft.edited_by && <> · edited by {draft.edited_by}</>}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {unsupported > 0 && <Badge tone="danger">{unsupported} unsupported</Badge>}
                {draft &&
                  open &&
                  !drafting &&
                  (editing ? (
                    <>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => {
                          setEditing(false);
                          setEdits({});
                        }}
                      >
                        Discard
                      </button>
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={!dirty || save.isPending}
                        onClick={() => save.mutate()}
                      >
                        {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        Save edits
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => setEditing(true)}
                    >
                      <Pencil className="h-4 w-4" /> Edit
                    </button>
                  ))}
              </div>
            </header>
            <div className="p-4">
              {draft ? (
                <EvidenceEditor
                  draft={draft}
                  editing={editing}
                  values={edits}
                  onChange={(f, v) => setEdits((e) => ({ ...e, [f]: v }))}
                />
              ) : drafting ? (
                <div className="space-y-2" aria-busy="true">
                  <div className="skeleton h-4 w-full" />
                  <div className="skeleton h-4 w-11/12" />
                  <div className="skeleton h-4 w-4/5" />
                </div>
              ) : (
                <p className="text-sm text-slate-500">No draft yet.</p>
              )}
              {draft && draft.missing_evidence.length > 0 && (
                <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                  Missing evidence: {draft.missing_evidence.join(', ')}
                </p>
              )}
            </div>
          </section>

          {open && (
            <form onSubmit={onRegenerate} className="card flex flex-col gap-2 p-4 sm:flex-row">
              <label htmlFor="instruction" className="sr-only">
                Instruction for the agent
              </label>
              <input
                id="instruction"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                maxLength={500}
                placeholder="Regenerate with an instruction, e.g. “stress the delivery photo”"
                className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
              />
              <button
                type="submit"
                className="btn-secondary"
                disabled={drafting || regenerate.isPending}
              >
                <RefreshCw
                  className={clsx('h-4 w-4', (drafting || regenerate.isPending) && 'animate-spin')}
                />
                Regenerate
              </button>
            </form>
          )}

          {d.outcome && (
            <section className="card p-4 text-sm">
              <h2 className="mb-1 font-semibold">Outcome</h2>
              Closed as <b>{d.outcome.result}</b> {formatAbsolute(d.outcome.closed_at)}
              {d.outcome.amount_recovered > 0 && (
                <>
                  {' '}
                  · <Money minor={d.outcome.amount_recovered} currency={d.currency} /> recovered
                </>
              )}
            </section>
          )}
        </div>
      </div>

      {open && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 p-3 backdrop-blur lg:static lg:rounded-xl lg:border lg:p-4 dark:border-slate-800 dark:bg-slate-900/95">
          <div className="mx-auto flex max-w-7xl flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
            <p className="mr-auto hidden text-sm text-slate-500 sm:block">
              {unsupported > 0
                ? 'Fix or remove unsupported sentences before submitting.'
                : 'Nothing reaches Stripe until you approve.'}
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={onAccept}
              disabled={drafting || propose.isPending}
            >
              Accept loss
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={onApprove}
              disabled={drafting || !draft || propose.isPending}
            >
              {propose.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              Approve &amp; submit
            </button>
          </div>
        </div>
      )}

      <ApproveDialog actionId={approveId} onClose={() => setApproveId(null)} />
    </div>
  );
}

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Bot, CornerDownLeft, Loader2, Search, ShieldCheck, Sparkles, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import {
  ApiError,
  proposeForDispute,
  regenerateDraft,
  runCommand,
  runRecon,
  type CommandResult,
  type ConfirmCard,
} from '../lib/api';
import { formatMoney } from '../lib/format';
import { Badge } from './ui';
import { ApproveDialog } from './workspace/ApproveDialog';

const EXAMPLES = [
  'Which disputes over $150 are due in 3 days?',
  "Why is yesterday's payout short?",
  'Which fraud warnings need a refund?',
  'Run reconciliation now',
];

function Card({
  card,
  onDone,
  onApprove,
}: {
  card: ConfirmCard;
  onDone: () => void;
  onApprove: (actionId: string) => void;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const go = useMutation({
    mutationFn: async () => {
      switch (card.kind) {
        case 'draft':
          await regenerateDraft(
            card.target_id!,
            String(card.params.instruction ?? '') || undefined,
          );
          navigate(`/disputes/${card.target_id}`);
          return 'The agent is redrafting';
        case 'recon': {
          const r = await runRecon();
          navigate('/reconciliation');
          return `Reconciliation: ${r.exception_count} exception(s)`;
        }
        case 'refund':
          onApprove(String(card.params.action_id));
          return null;
        default: {
          const a = await proposeForDispute(
            card.target_id!,
            card.kind === 'submit' ? 'submit' : 'accept',
          );
          onApprove(a.id);
          return null;
        }
      }
    },
    onSuccess: (msg) => {
      if (msg) {
        toast.success(msg);
        onDone();
      }
      void qc.invalidateQueries();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not run that'),
  });
  const needsGate = card.kind === 'submit' || card.kind === 'accept' || card.kind === 'refund';
  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/50 p-3 dark:border-brand-500/30 dark:bg-brand-500/5">
      <div className="flex items-start gap-2">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{card.title}</div>
          <p className="text-xs text-slate-600 dark:text-slate-300">{card.description}</p>
          {needsGate && (
            <p className="mt-1 text-[11px] text-slate-500">
              Next: policy checks and your approval.
            </p>
          )}
        </div>
      </div>
      <div className="mt-2 flex justify-end gap-2">
        <button type="button" className="btn-secondary py-1.5 text-xs" onClick={onDone}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary py-1.5 text-xs"
          disabled={go.isPending}
          onClick={() => go.mutate()}
        >
          {go.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {needsGate ? 'Review & approve' : 'Confirm'}
        </button>
      </div>
    </div>
  );
}

export function CommandBar() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<CommandResult | null>(null);
  const [approveId, setApproveId] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const ask = useMutation({
    mutationFn: runCommand,
    onSuccess: setResult,
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'The agent could not answer'),
  });

  const close = useCallback(() => {
    setOpen(false);
    setResult(null);
    setQuery('');
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [close]);
  useEffect(() => {
    if (open) setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  const submit = (e?: FormEvent, q = query) => {
    e?.preventDefault();
    if (q.trim().length >= 2) ask.mutate(q.trim());
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn-secondary h-9 gap-2 px-3 text-slate-500"
        aria-label="Open command bar"
        aria-keyshortcuts="Control+K Meta+K"
      >
        <Search className="h-4 w-4" />
        <span className="hidden sm:inline">Ask Recoup</span>
        <kbd className="hidden rounded border border-slate-300 px-1 font-mono text-[10px] sm:inline dark:border-slate-600">
          ⌘K
        </kbd>
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center p-3 pt-[10vh]">
          <div className="absolute inset-0 bg-slate-900/60" onClick={close} aria-hidden />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Command bar"
            className="card relative w-full max-w-xl overflow-hidden"
          >
            <form
              onSubmit={submit}
              className="flex items-center gap-2 border-b border-slate-200 px-4 dark:border-slate-800"
            >
              <Sparkles className="h-4 w-4 shrink-0 text-brand-600" aria-hidden />
              <label htmlFor="cmd" className="sr-only">
                Ask Recoup
              </label>
              <input
                ref={input}
                id="cmd"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Ask about disputes, payouts, fraud warnings… or tell me what to do"
                className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400"
                autoComplete="off"
              />
              {ask.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin text-slate-400" aria-label="Thinking" />
              ) : (
                <CornerDownLeft className="h-4 w-4 text-slate-400" aria-hidden />
              )}
              <button
                type="button"
                onClick={close}
                className="rounded p-1 text-slate-400 hover:text-slate-600"
                aria-label="Close command bar"
              >
                <X className="h-4 w-4" />
              </button>
            </form>

            <div className="max-h-[60vh] overflow-y-auto p-4" aria-live="polite">
              {!result && !ask.isPending && (
                <div>
                  <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                    Try
                  </p>
                  <ul className="space-y-1">
                    {EXAMPLES.map((ex) => (
                      <li key={ex}>
                        <button
                          type="button"
                          className="w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800"
                          onClick={() => {
                            setQuery(ex);
                            submit(undefined, ex);
                          }}
                        >
                          {ex}
                        </button>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-3 text-xs text-slate-500">
                    Reads run right away. Anything that changes money asks you to confirm first.
                  </p>
                </div>
              )}
              {ask.isPending && (
                <div className="space-y-2" aria-busy="true">
                  <div className="skeleton h-4 w-3/4" />
                  <div className="skeleton h-4 w-1/2" />
                </div>
              )}
              {result && (
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <Bot className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" aria-hidden />
                    <p className="text-sm leading-relaxed">{result.answer}</p>
                  </div>
                  {result.cards.map((card, i) => (
                    <Card key={i} card={card} onDone={close} onApprove={(id) => setApproveId(id)} />
                  ))}
                  {result.records.length > 0 && (
                    <ul className="space-y-1">
                      {result.records.slice(0, 8).map((r) => {
                        const amount = typeof r.data.amount === 'number' ? r.data.amount : null;
                        const link =
                          r.kind === 'dispute'
                            ? `/disputes/${r.id}`
                            : r.kind === 'recon_exception'
                              ? '/reconciliation'
                              : r.kind === 'fraud_warning'
                                ? '/'
                                : null;
                        return (
                          <li key={r.id}>
                            <button
                              type="button"
                              disabled={!link}
                              onClick={() => {
                                if (link) {
                                  navigate(link);
                                  close();
                                }
                              }}
                              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-slate-100 disabled:hover:bg-transparent dark:hover:bg-slate-800"
                            >
                              <Badge>{r.kind.replace('_', ' ')}</Badge>
                              <span className="truncate font-mono">{r.id}</span>
                              {amount != null && (
                                <span className="tabular ml-auto">
                                  {formatMoney(Math.abs(amount))}
                                </span>
                              )}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  <p className="text-[11px] text-slate-400">
                    {result.model} · tools: {result.tool_calls.join(', ') || 'none'}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      <ApproveDialog
        actionId={approveId}
        onClose={() => {
          setApproveId(null);
          close();
        }}
      />
    </>
  );
}

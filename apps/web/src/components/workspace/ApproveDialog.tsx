import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2, ShieldCheck, XCircle } from 'lucide-react';
import { toast } from 'sonner';

import { ApiError, approveAction, getAction } from '../../lib/api';
import { ACTION_LABEL, CHECK_LABEL } from '../../lib/labels';
import { Dialog, Money } from '../ui';

/**
 * Human approval step. Loads the action fresh from the API: re-run policy checks plus an
 * approval token bound to this user and the exact payload hash. Confirm is disabled unless
 * every check passes; the server re-checks anyway.
 */
export function ApproveDialog({
  actionId,
  onClose,
}: {
  actionId: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: ['action', actionId],
    queryFn: () => getAction(actionId!),
    enabled: !!actionId,
    staleTime: 0,
  });
  const approve = useMutation({
    mutationFn: () => approveAction(actionId!, view.data!.approval_token!),
    onSuccess: (a) => {
      if (a.status === 'executed') {
        toast.success(ACTION_LABEL[a.type] ?? 'Done', {
          description:
            a.type === 'submit_dispute_evidence'
              ? 'Evidence submitted to Stripe.'
              : a.type === 'accept_dispute'
                ? 'Dispute closed in Stripe.'
                : a.type === 'refund_charge'
                  ? 'Refunded in Stripe; the customer is emailed.'
                  : undefined,
        });
      } else {
        toast.error(`Blocked by policy`, { description: a.error ?? a.status });
      }
      void qc.invalidateQueries(); // dispute, list, KPIs, fraud warnings, outbox all move
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Approval failed'),
  });

  const a = view.data?.action;
  const failed = a?.policy_checks.filter((c) => !c.passed) ?? [];
  const evidence = (a?.payload?.evidence ?? {}) as Record<string, string>;
  const forced = a?.payload?.force_test_outcome as string | null | undefined;

  return (
    <Dialog
      open={!!actionId}
      onClose={onClose}
      title={a ? (ACTION_LABEL[a.type] ?? a.type) : 'Approve action'}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={a?.type === 'accept_dispute' ? 'btn-danger' : 'btn-primary'}
            disabled={!a || failed.length > 0 || !view.data?.approval_token || approve.isPending}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ShieldCheck className="h-4 w-4" />
            )}
            Approve
            {a?.amount != null && (
              <>
                {' '}
                · <Money minor={a.amount} />
              </>
            )}
          </button>
        </>
      }
    >
      {view.isLoading || !a ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton h-6" />
          ))}
        </div>
      ) : (
        <div className="space-y-4 text-sm">
          <p className="text-slate-600 dark:text-slate-300">{a.reason}</p>
          {a.type === 'submit_dispute_evidence' && (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
              {Object.keys(evidence).length} evidence fields will be sent with{' '}
              <code className="font-mono text-xs">submit: true</code>. Stripe allows one submission,
              so check the draft first.
              {forced && (
                <span className="mt-1 block text-xs text-amber-700 dark:text-amber-300">
                  Test mode: the payload asks Stripe to close this dispute as <b>{forced}</b>.
                </span>
              )}
            </p>
          )}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Policy checks
            </h3>
            <ul className="space-y-1.5">
              {a.policy_checks.map((c) => (
                <li key={c.name} className="flex items-start gap-2">
                  {c.passed ? (
                    <CheckCircle2
                      className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500"
                      aria-label="passed"
                    />
                  ) : (
                    <XCircle
                      className="mt-0.5 h-4 w-4 shrink-0 text-rose-500"
                      aria-label="failed"
                    />
                  )}
                  <span>
                    {CHECK_LABEL[c.name] ?? c.name}
                    {!c.passed && c.detail && (
                      <span className="block text-xs text-rose-600">{c.detail}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <p className="font-mono text-[11px] text-slate-400">
            {a.id} · hash {a.hash.slice(0, 12)}…
          </p>
        </div>
      )}
    </Dialog>
  );
}

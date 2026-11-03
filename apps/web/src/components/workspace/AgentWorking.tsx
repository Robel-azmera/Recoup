import type { AuditEntry } from '@recoup/shared';
import { Bot, CheckCircle2, Loader2 } from 'lucide-react';

import { useAuditFeed } from '../../hooks/useAuditFeed';

function label(e: AuditEntry): string {
  const out = (e.output ?? {}) as Record<string, unknown>;
  switch (e.step) {
    case 'agent.started':
      return `Started (${String((e.input as Record<string, unknown> | null)?.playbook ?? 'playbook')})`;
    case 'agent.tool_call':
      return `${e.tool}: ${String(out.summary ?? '')}`;
    case 'agent.checklist':
      return `Checklist built: baseline score ${String(out.baseline)}`;
    case 'agent.cache_hit':
      return 'Reused cached analysis (nothing changed)';
    case 'agent.llm_fallback':
      return 'Model unavailable: drafting from records deterministically';
    case 'action.proposed':
      return 'Proposed the next action for approval';
    case 'agent.completed':
      return `Done: win score ${String(out.win_score)}, recommends ${String(out.recommendation)}`;
    case 'agent.failed':
      return `Failed: ${String(out.error ?? '')}`;
    default:
      return e.step;
  }
}

/** Streams the agent's steps for one dispute from audit_log while it drafts. */
export function AgentWorking({ disputeId, active }: { disputeId: string; active: boolean }) {
  const { entries } = useAuditFeed(disputeId, 30);
  const all = [...(entries ?? [])].reverse(); // oldest first
  const start = all.map((e) => e.step).lastIndexOf('agent.started');
  const run = start >= 0 ? all.slice(start) : [];
  if (!active && run.length === 0) return null;

  return (
    <section
      className="card border-brand-200 bg-brand-50/40 p-4 dark:border-brand-500/30 dark:bg-brand-500/5"
      aria-live="polite"
      aria-label="Agent progress"
    >
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
        {active ? (
          <Loader2 className="h-4 w-4 animate-spin text-brand-600" aria-hidden />
        ) : (
          <Bot className="h-4 w-4 text-brand-600" aria-hidden />
        )}
        {active ? 'Agent is working…' : 'Last agent run'}
      </div>
      <ol className="space-y-1 text-sm">
        {run.length === 0 && <li className="skeleton h-5 w-2/3" />}
        {run.map((e) => (
          <li key={e.id} className="flex items-start gap-2">
            <CheckCircle2
              className={
                e.level === 'error'
                  ? 'mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-500'
                  : 'mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500'
              }
              aria-hidden
            />
            <span className="min-w-0 break-words text-slate-700 dark:text-slate-300">
              {label(e)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

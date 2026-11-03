import type { AppSettings } from '@recoup/shared';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, SlidersHorizontal } from 'lucide-react';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { toast } from 'sonner';

import { api, ApiError } from '../lib/api';

const getSettings = () => api<AppSettings>('/settings');
const putSettings = (s: AppSettings) =>
  api<AppSettings>('/settings', { method: 'PUT', body: JSON.stringify(s) });

function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

const input =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950';

function Toggle({
  id,
  checked,
  onChange,
  label,
}: {
  id: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-slate-300"
      />
      {label}
    </label>
  );
}

/** Settings & Policies: thresholds the policy gate enforces, auto-policies, brief channels. */
export function PoliciesForm() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['settings'], queryFn: getSettings });
  const [s, setS] = useState<AppSettings | null>(null);
  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  const save = useMutation({
    mutationFn: putSettings,
    onSuccess: (r) => {
      toast.success('Policies saved', {
        description: 'The policy gate uses them from the next check.',
      });
      qc.setQueryData(['settings'], r);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Save failed'),
  });

  if (!s)
    return (
      <section className="card space-y-2 p-4">
        <div className="skeleton h-6 w-40" />
        <div className="skeleton h-24" />
      </section>
    );

  const dollars = (cents: number) => (cents / 100).toString();
  const cents = (v: string) => Math.max(0, Math.round(Number(v || 0) * 100));
  const set = (fn: (d: AppSettings) => void) =>
    setS((prev) => {
      const next = structuredClone(prev!);
      fn(next);
      return next;
    });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(s);
  };

  return (
    <form onSubmit={submit} className="card space-y-5 p-4" aria-labelledby="policies-heading">
      <h2 id="policies-heading" className="flex items-center gap-2 text-sm font-semibold">
        <SlidersHorizontal className="h-4 w-4 text-brand-600" aria-hidden /> Policies
      </h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          id="p-fight"
          label="Fight minimum ($)"
          hint="Below this, the agent recommends accepting (fees exceed value)."
        >
          <input
            id="p-fight"
            type="number"
            min={0}
            step="0.01"
            className={input}
            value={dollars(s.thresholds.fight_min_amount)}
            onChange={(e) => set((d) => (d.thresholds.fight_min_amount = cents(e.target.value)))}
          />
        </Field>
        <Field
          id="p-auto"
          label="Auto-refund limit for fraud warnings ($)"
          hint="Only applies when auto-refund is on."
        >
          <input
            id="p-auto"
            type="number"
            min={0}
            step="0.01"
            className={input}
            value={dollars(s.thresholds.auto_refund_max_amount)}
            onChange={(e) =>
              set((d) => (d.thresholds.auto_refund_max_amount = cents(e.target.value)))
            }
          />
        </Field>
        <Field
          id="p-ceiling"
          label="Refund ceiling ($)"
          hint="Hard limit for any refund, approved or not."
        >
          <input
            id="p-ceiling"
            type="number"
            min={0}
            step="0.01"
            className={input}
            value={dollars(s.thresholds.max_refund_amount)}
            onChange={(e) => set((d) => (d.thresholds.max_refund_amount = cents(e.target.value)))}
          />
        </Field>
        <Field id="p-hours" label="Deadline alert window (hours)">
          <input
            id="p-hours"
            type="number"
            min={1}
            max={240}
            className={input}
            value={s.thresholds.deadline_alert_hours}
            onChange={(e) =>
              set((d) => (d.thresholds.deadline_alert_hours = Number(e.target.value)))
            }
          />
        </Field>
        <Field id="p-email" label="Owner email (brief + escalations)">
          <input
            id="p-email"
            type="email"
            className={input}
            value={s.channels.owner_email ?? ''}
            onChange={(e) => set((d) => (d.channels.owner_email = e.target.value || null))}
          />
        </Field>
        <Field
          id="p-outcome"
          label="Test-mode outcome on submit"
          hint="Appends Stripe's magic evidence value so test disputes close."
        >
          <select
            id="p-outcome"
            className={input}
            value={s.demo.force_test_outcome ?? ''}
            onChange={(e) =>
              set(
                (d) =>
                  (d.demo.force_test_outcome = (e.target.value ||
                    null) as AppSettings['demo']['force_test_outcome']),
              )
            }
          >
            <option value="won">Won</option>
            <option value="lost">Lost</option>
            <option value="">None (stays under review)</option>
          </select>
        </Field>
      </div>
      <fieldset className="space-y-2">
        <legend className="mb-1 text-sm font-medium">Automation</legend>
        <Toggle
          id="p-autoefw"
          label="Auto-refund fraud warnings under the limit (no human approval)"
          checked={s.auto_policies.auto_refund_efw}
          onChange={(v) => set((d) => (d.auto_policies.auto_refund_efw = v))}
        />
        <Toggle
          id="p-tg"
          label="Send the morning brief to Telegram"
          checked={s.channels.brief_telegram}
          onChange={(v) => set((d) => (d.channels.brief_telegram = v))}
        />
        <Toggle
          id="p-em"
          label="Send the morning brief by email"
          checked={s.channels.brief_email}
          onChange={(v) => set((d) => (d.channels.brief_email = v))}
        />
      </fieldset>
      <div className="flex justify-end">
        <button type="submit" className="btn-primary" disabled={save.isPending}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save policies
        </button>
      </div>
    </form>
  );
}

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FlaskConical, Loader2, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { ActivityFeed } from '../components/ActivityFeed';
import { IntegrationStatus } from '../components/IntegrationStatus';
import { Outbox } from '../components/Outbox';
import { PoliciesForm } from '../components/PoliciesForm';
import { ApiError, createDemoCharge, syncNow, type DemoScenario } from '../lib/api';
import { formatMoney } from '../lib/format';

const SCENARIOS: { id: DemoScenario; label: string; hint: string }[] = [
  { id: 'fraudulent', label: 'Fraudulent dispute', hint: 'pm_card_createDispute' },
  {
    id: 'product_not_received',
    label: 'Product not received',
    hint: 'pm_card_createDisputeProductNotReceived',
  },
  { id: 'inquiry', label: 'Inquiry', hint: 'pm_card_createDisputeInquiry' },
  {
    id: 'early_fraud_warning',
    label: 'Early fraud warning',
    hint: 'pm_card_createIssuerFraudRecord',
  },
];

function DemoConsole() {
  const qc = useQueryClient();
  const [amount, setAmount] = useState('');
  const charge = useMutation({
    mutationFn: (s: DemoScenario) =>
      createDemoCharge(s, amount ? Math.round(Number(amount) * 100) : undefined),
    onSuccess: (r) => {
      toast.success(
        `${r.dispute_id ? 'Dispute created' : 'Charge created'}: ${formatMoney(r.amount)}`,
        {
          description: r.message,
        },
      );
      void qc.invalidateQueries({ queryKey: ['disputes'] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Demo charge failed'),
  });
  const sync = useMutation({
    mutationFn: syncNow,
    onSuccess: (r) =>
      toast.success('Stripe sync complete', {
        description: Object.entries(r)
          .map(([k, v]) => `${k}: ${v}`)
          .join(' · '),
      }),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Sync failed'),
  });

  return (
    <section className="card p-4" aria-labelledby="demo-heading">
      <div className="mb-1 flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-brand-600" aria-hidden />
        <h2 id="demo-heading" className="text-sm font-semibold">
          Demo Console
        </h2>
      </div>
      <p className="mb-4 text-sm text-slate-500">
        Creates a new order and charges it with one of Stripe&apos;s test cards. Stripe turns it
        into a dispute or early fraud warning, and the webhook kicks off the agent.
      </p>
      <div className="mb-4 max-w-xs space-y-1.5">
        <label htmlFor="demo-amount" className="text-sm font-medium">
          Amount (USD, optional)
        </label>
        <input
          id="demo-amount"
          type="number"
          min={1}
          step="0.01"
          inputMode="decimal"
          placeholder="Catalog price"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
        />
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {SCENARIOS.map((s) => {
          const busy = charge.isPending && charge.variables === s.id;
          return (
            <button
              key={s.id}
              type="button"
              className="btn-secondary h-auto flex-col items-start py-2.5 text-left"
              disabled={charge.isPending}
              onClick={() => charge.mutate(s.id)}
            >
              <span className="flex items-center gap-2">
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                {s.label}
              </span>
              <span className="font-mono text-[11px] font-normal text-slate-500">{s.hint}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-4 border-t border-slate-200 pt-4 dark:border-slate-800">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
        >
          <RefreshCw className={sync.isPending ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          Sync from Stripe now
        </button>
      </div>
    </section>
  );
}

export function Settings() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-slate-500">
          Integrations, policies the gate enforces, and demo tools.
        </p>
      </div>
      <IntegrationStatus />
      <PoliciesForm />
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <DemoConsole />
          <ActivityFeed title="Live events" />
        </div>
        <Outbox />
      </div>
    </div>
  );
}

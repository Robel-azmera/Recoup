import { useQuery } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';
import { Link } from 'react-router-dom';

import { ActivityFeed } from '../components/ActivityFeed';
import { IntegrationStatus } from '../components/IntegrationStatus';
import { listDisputes } from '../lib/api';
import { formatMoney } from '../lib/format';

export function Overview() {
  const disputes = useQuery({ queryKey: ['disputes'], queryFn: listDisputes });
  const open = disputes.data?.filter((d) => !['won', 'lost', 'accepted'].includes(d.status)) ?? [];
  const atRisk = open.reduce((s, d) => s + d.amount, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="text-sm text-slate-500">Full KPI dashboard arrives in Phase 6.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Link to="/disputes" className="card p-4 transition hover:border-brand-500">
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <ShieldAlert className="h-4 w-4" aria-hidden /> Open disputes
          </div>
          {disputes.isLoading ? (
            <div className="skeleton mt-2 h-8 w-16" />
          ) : (
            <div className="tabular mt-1 text-2xl font-semibold">{open.length}</div>
          )}
        </Link>
        <div className="card p-4">
          <div className="text-sm text-slate-500">$ at risk</div>
          {disputes.isLoading ? (
            <div className="skeleton mt-2 h-8 w-28" />
          ) : (
            <div className="tabular mt-1 text-2xl font-semibold">{formatMoney(atRisk)}</div>
          )}
        </div>
      </div>
      <ActivityFeed />
      <IntegrationStatus />
    </div>
  );
}

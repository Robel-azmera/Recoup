import { Construction } from 'lucide-react';

import { EmptyState } from '../components/EmptyState';

export function Placeholder({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="card">
        <EmptyState icon={Construction} title="Coming soon">
          This screen lands in {phase}.
        </EmptyState>
      </div>
    </div>
  );
}

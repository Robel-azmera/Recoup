import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { Kpis } from '../lib/api';
import { formatMoney } from '../lib/format';
import { CATEGORY_LABEL } from '../lib/labels';

type Row = { label: string; amount: number; count: number };

function TooltipBody({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const r = payload[0]!.payload;
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md dark:border-slate-700 dark:bg-slate-900">
      <div className="font-medium">{r.label}</div>
      <div className="tabular text-slate-600 dark:text-slate-300">
        {formatMoney(r.amount)} · {r.count} dispute{r.count === 1 ? '' : 's'}
      </div>
    </div>
  );
}

/** Disputed $ by reason. Single series: one hue, no legend (the title names it), direct labels. */
export function CategoryChart({ data }: { data: Kpis['by_category'] }) {
  const rows: Row[] = data.map((d) => ({
    label: CATEGORY_LABEL[d.category as keyof typeof CATEGORY_LABEL] ?? d.category,
    amount: d.amount,
    count: d.count,
  }));
  if (!rows.length)
    return <p className="py-10 text-center text-sm text-slate-500">No disputes yet.</p>;
  return (
    <figure>
      <div style={{ height: Math.max(140, rows.length * 44) }} aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 0, right: 72, bottom: 0, left: 0 }}
            barCategoryGap={10}
          >
            <XAxis type="number" hide domain={[0, 'dataMax']} />
            <YAxis
              type="category"
              dataKey="label"
              width={120}
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'var(--chart-text)', fontSize: 12 }}
            />
            <Tooltip
              content={<TooltipBody />}
              cursor={{ fill: 'var(--chart-grid)', opacity: 0.5 }}
            />
            <Bar dataKey="amount" fill="var(--series-1)" radius={[0, 4, 4, 0]} maxBarSize={22}>
              <LabelList
                dataKey="amount"
                position="right"
                formatter={(v: number) => formatMoney(v, 'usd', { compact: v >= 100_000 })}
                style={{
                  fill: 'var(--chart-text)',
                  fontSize: 12,
                  fontVariantNumeric: 'tabular-nums',
                }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <table className="sr-only">
        <caption>Disputed amount by reason</caption>
        <thead>
          <tr>
            <th>Reason</th>
            <th>Amount</th>
            <th>Disputes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td>{formatMoney(r.amount)}</td>
              <td>{r.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

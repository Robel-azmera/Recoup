import clsx from 'clsx';
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  CreditCard,
  MessageSquare,
  Package,
  ShoppingCart,
  Truck,
  type LucideIcon,
} from 'lucide-react';

import type { DisputeDetail } from '../../lib/api';
import { formatAbsolute, formatMoney } from '../../lib/format';
import { RelativeTime } from '../RelativeTime';

interface Event {
  at: string;
  icon: LucideIcon;
  title: string;
  body?: string;
  tone?: 'default' | 'good' | 'warn' | 'bad';
  quote?: boolean;
  link?: string;
}

export function buildTimeline({ dispute: d, order, messages, charge }: DisputeDetail): Event[] {
  const ev: Event[] = [];
  if (order) {
    ev.push({
      at: order.created_at,
      icon: ShoppingCart,
      title: `Order ${order.id} placed`,
      body: `${order.items.map((i) => `${i.quantity}× ${i.name}`).join(', ')} · ${formatMoney(order.total, order.currency)}${order.ip ? ` · IP ${order.ip}` : ''}`,
    });
  }
  if (charge) {
    const r = charge.risk;
    const allPass =
      r?.cvc_check === 'pass' &&
      r?.address_line1_check === 'pass' &&
      r?.address_postal_code_check === 'pass';
    ev.push({
      at: charge.created,
      icon: CreditCard,
      title: `Payment ${charge.card ? `· ${charge.card.brand} •••• ${charge.card.last4}` : ''}`,
      body: `CVC ${r?.cvc_check ?? 'n/a'} · AVS ${r?.address_line1_check ?? 'n/a'}/${r?.address_postal_code_check ?? 'n/a'} · Radar ${r?.risk_level ?? 'n/a'}${r?.risk_score != null ? ` (${r.risk_score})` : ''}`,
      tone: allPass ? 'good' : 'warn',
    });
  }
  const f = order?.fulfilment;
  if (f?.shipped_at && !order?.digital) {
    ev.push({
      at: f.shipped_at,
      icon: Truck,
      title: `Shipped with ${f.carrier ?? 'carrier'}`,
      body: f.tracking ?? undefined,
    });
  }
  if (f?.delivered_at) {
    ev.push({
      at: f.delivered_at,
      icon: order?.digital ? CheckCircle2 : Package,
      title: order?.digital ? 'License delivered and activated' : 'Delivered',
      body:
        [
          f.signed_by && `Signed by ${f.signed_by}`,
          f.delivered_to_billing_address && 'to the billing address',
        ]
          .filter(Boolean)
          .join(' · ') || undefined,
      tone: 'good',
      link: f.proof_url ?? undefined,
    });
  }
  for (const m of messages) {
    ev.push({
      at: m.sent_at,
      icon: MessageSquare,
      title:
        m.direction === 'inbound' ? `Customer (${m.channel})` : `Merchant reply (${m.channel})`,
      body: m.body,
      quote: true,
    });
  }
  ev.push({
    at: d.created_at,
    icon: AlertTriangle,
    title: `Dispute opened · ${d.reason_code}`,
    tone: 'bad',
  });
  if (d.outcome) {
    ev.push({
      at: d.outcome.closed_at,
      icon: CheckCircle2,
      title: `Closed: ${d.outcome.result}`,
      body: d.outcome.amount_recovered
        ? `${formatMoney(d.outcome.amount_recovered, d.currency)} recovered`
        : undefined,
      tone: d.outcome.result === 'won' ? 'good' : 'bad',
    });
  } else {
    ev.push({ at: d.due_by, icon: CalendarClock, title: 'Response due', tone: 'warn' });
  }
  return ev.sort((a, b) => a.at.localeCompare(b.at));
}

const TONE = {
  default: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
  good: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  warn: 'bg-amber-100 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300',
  bad: 'bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
};

export function Timeline({ detail }: { detail: DisputeDetail }) {
  const events = buildTimeline(detail);
  return (
    <section className="card p-4" aria-labelledby="timeline-heading">
      <h2 id="timeline-heading" className="mb-4 text-sm font-semibold">
        Timeline
      </h2>
      <ol className="relative space-y-5 before:absolute before:bottom-2 before:left-[15px] before:top-2 before:w-px before:bg-slate-200 dark:before:bg-slate-800">
        {events.map((e, i) => (
          <li key={i} className="relative flex gap-3">
            <span
              className={clsx(
                'z-10 grid h-8 w-8 shrink-0 place-items-center rounded-full',
                TONE[e.tone ?? 'default'],
              )}
            >
              <e.icon className="h-4 w-4" aria-hidden />
            </span>
            <div className="min-w-0 flex-1 pt-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <p className="text-sm font-medium">{e.title}</p>
                <span className="text-xs text-slate-500" title={formatAbsolute(e.at)}>
                  <RelativeTime value={e.at} />
                </span>
              </div>
              {e.body &&
                (e.quote ? (
                  <blockquote className="mt-1 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700 dark:bg-slate-800/60 dark:text-slate-300">
                    {e.body}
                  </blockquote>
                ) : (
                  <p className="mt-0.5 break-words text-sm text-slate-500">{e.body}</p>
                ))}
              {e.link && (
                <a
                  href={e.link}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-block text-xs font-medium text-brand-600 hover:underline"
                >
                  Proof of delivery ↗
                </a>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

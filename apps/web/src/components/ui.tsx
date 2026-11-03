import type { DisputeStatus } from '@recoup/shared';
import clsx from 'clsx';
import { X } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { countdown, formatAbsolute, formatMoney } from '../lib/format';
import { STATUS_LABEL } from '../lib/labels';

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info';

const TONES: Record<Tone, string> = {
  neutral: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  brand: 'bg-brand-50 text-brand-700 dark:bg-brand-500/10 dark:text-brand-100',
  success: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300',
  warning: 'bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300',
  danger: 'bg-rose-100 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
  info: 'bg-sky-100 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS_TONE: Record<DisputeStatus, Tone> = {
  needs_response: 'warning',
  drafting: 'info',
  awaiting_approval: 'brand',
  submitted: 'neutral',
  accepted: 'neutral',
  won: 'success',
  lost: 'danger',
};

export function StatusBadge({ status }: { status: DisputeStatus }) {
  return (
    <Badge tone={STATUS_TONE[status]}>
      {status === 'drafting' && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" aria-hidden />
      )}
      {STATUS_LABEL[status]}
    </Badge>
  );
}

export function Money({
  minor,
  currency = 'usd',
  className,
}: {
  minor: number;
  currency?: string;
  className?: string;
}) {
  return <span className={clsx('tabular', className)}>{formatMoney(minor, currency)}</span>;
}

/** Deadline countdown, refreshed every minute; absolute date on hover. */
export function Countdown({ due, done = false }: { due: string; done?: boolean }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 60_000);
    return () => clearInterval(id);
  }, []);
  if (done) {
    return (
      <time dateTime={due} title={formatAbsolute(due)} className="text-sm text-slate-500">
        —
      </time>
    );
  }
  const c = countdown(due);
  return (
    <time
      dateTime={due}
      title={`Due ${formatAbsolute(due)}`}
      className={clsx(
        'tabular whitespace-nowrap text-sm font-medium',
        c.urgency === 'overdue' && 'text-rose-600 dark:text-rose-400',
        c.urgency === 'critical' && 'text-rose-600 dark:text-rose-400',
        c.urgency === 'soon' && 'text-amber-600 dark:text-amber-400',
        c.urgency === 'ok' && 'text-slate-700 dark:text-slate-300',
      )}
    >
      {c.label}
    </time>
  );
}

export function scoreTone(score: number): Tone {
  return score >= 70 ? 'success' : score >= 45 ? 'warning' : 'danger';
}

export function ScoreRing({
  score,
  size = 56,
}: {
  score: number | null | undefined;
  size?: number;
}) {
  const r = size / 2 - 5;
  const circ = 2 * Math.PI * r;
  const value = score ?? 0;
  const tone = scoreTone(value);
  const color =
    tone === 'success'
      ? 'stroke-emerald-500'
      : tone === 'warning'
        ? 'stroke-amber-500'
        : 'stroke-rose-500';
  return (
    <div
      className="relative inline-grid place-items-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={score == null ? 'No win score yet' : `Win likelihood ${score} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          className="fill-none stroke-slate-200 dark:stroke-slate-800"
          strokeWidth={5}
        />
        {score != null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            className={clsx('fill-none transition-all duration-700', color)}
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={circ}
            strokeDashoffset={circ * (1 - value / 100)}
          />
        )}
      </svg>
      <span className="tabular absolute text-sm font-semibold">{score ?? '–'}</span>
    </div>
  );
}

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      prev?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div className="absolute inset-0 bg-slate-900/60" onClick={onClose} aria-hidden />
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        className="card relative flex max-h-[90vh] w-full flex-col rounded-b-none outline-none sm:max-w-lg sm:rounded-xl"
      >
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3 dark:border-slate-800">
          <h2 id="dialog-title" className="text-base font-semibold">
            {title}
          </h2>
          <button
            type="button"
            className="btn-secondary h-8 w-8 p-0"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
        {footer && (
          <footer className="flex flex-col-reverse gap-2 border-t border-slate-200 px-5 py-3 sm:flex-row sm:justify-end dark:border-slate-800">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

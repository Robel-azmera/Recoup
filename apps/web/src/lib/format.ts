const moneyFormatters = new Map<string, Intl.NumberFormat>();

/** Format integer minor units (cents) as currency, e.g. 12345, "usd" -> "$123.45". */
export function formatMoney(
  minor: number,
  currency = 'usd',
  opts: { compact?: boolean } = {},
): string {
  const key = `${currency}:${opts.compact ? 'c' : ''}`;
  let f = moneyFormatters.get(key);
  if (!f) {
    f = new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
      notation: opts.compact ? 'compact' : 'standard',
      maximumFractionDigits: opts.compact ? 1 : 2,
    });
    moneyFormatters.set(key, f);
  }
  return f.format(minor / 100);
}

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400_000],
  ['hour', 3_600_000],
  ['minute', 60_000],
  ['second', 1000],
];

/** "in 3 days", "2 hours ago". */
export function formatRelative(date: string | Date, now: Date = new Date()): string {
  const diff = new Date(date).getTime() - now.getTime();
  for (const [unit, ms] of UNITS) {
    if (Math.abs(diff) >= ms || unit === 'second') return rtf.format(Math.round(diff / ms), unit);
  }
  return rtf.format(0, 'second');
}

const absFmt = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' });

export function formatAbsolute(date: string | Date): string {
  return absFmt.format(new Date(date));
}

/** Countdown parts for deadline display; negative = overdue. */
export function countdown(due: string | Date, now: Date = new Date()) {
  const ms = new Date(due).getTime() - now.getTime();
  const abs = Math.abs(ms);
  const days = Math.floor(abs / 86_400_000);
  const hours = Math.floor((abs % 86_400_000) / 3_600_000);
  const label =
    days > 0 ? `${days}d ${hours}h` : `${hours}h ${Math.floor((abs % 3_600_000) / 60_000)}m`;
  const urgency: 'overdue' | 'critical' | 'soon' | 'ok' =
    ms < 0 ? 'overdue' : ms < 48 * 3_600_000 ? 'critical' : ms < 72 * 3_600_000 ? 'soon' : 'ok';
  return { ms, label: ms < 0 ? `${label} overdue` : label, urgency };
}

export function formatPercent(ratio: number, digits = 1): string {
  return new Intl.NumberFormat('en-US', {
    style: 'percent',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(ratio);
}

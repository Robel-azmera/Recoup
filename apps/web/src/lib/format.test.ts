import { countdown, formatMoney, formatPercent, formatRelative } from './format';

describe('format', () => {
  it('formats minor units as currency', () => {
    expect(formatMoney(12345)).toBe('$123.45');
    expect(formatMoney(-800, 'usd')).toBe('-$8.00');
    expect(formatMoney(1_250_000, 'usd', { compact: true })).toBe('$12.5K');
  });

  it('formats relative times', () => {
    const now = new Date('2026-11-02T12:00:00Z');
    expect(formatRelative('2026-11-05T12:00:00Z', now)).toBe('in 3 days');
    expect(formatRelative('2026-11-02T10:00:00Z', now)).toBe('2 hours ago');
  });

  it('computes deadline urgency', () => {
    const now = new Date('2026-11-02T12:00:00Z');
    expect(countdown('2026-11-03T12:00:00Z', now).urgency).toBe('critical');
    expect(countdown('2026-11-04T18:00:00Z', now).urgency).toBe('soon');
    expect(countdown('2026-11-10T12:00:00Z', now)).toMatchObject({ urgency: 'ok', label: '8d 0h' });
    expect(countdown('2026-11-02T11:00:00Z', now).urgency).toBe('overdue');
  });

  it('formats percents', () => {
    expect(formatPercent(0.009)).toBe('0.9%');
  });
});

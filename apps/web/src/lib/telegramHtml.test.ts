import { sanitizeTelegramHtml } from './telegramHtml';

describe('sanitizeTelegramHtml', () => {
  it('keeps the Telegram subset', () => {
    expect(sanitizeTelegramHtml('<b>$249.00</b> · <code>dp_1</code>')).toBe(
      '<b>$249.00</b> · <code>dp_1</code>',
    );
    expect(sanitizeTelegramHtml('<a href="https://x.test/d">open</a>')).toBe(
      '<a href="https://x.test/d" target="_blank" rel="noreferrer">open</a>',
    );
  });

  it('drops scripts, handlers and unsafe links', () => {
    const out = sanitizeTelegramHtml(
      '<img src=x onerror="alert(1)"><script>alert(2)</script><a href="javascript:alert(3)">x</a><b onclick="e()">y</b>',
    );
    expect(out).not.toMatch(/onerror|script|javascript|onclick|<img/i);
    expect(out).toContain('<b>y</b>');
  });
});

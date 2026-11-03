/**
 * Render the small HTML subset Telegram messages use (<b>, <code>, <a href>) safely:
 * parse, then rebuild with an allow-list. Anything else becomes plain text.
 */
export function sanitizeTelegramHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html');
  const esc = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const walk = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return esc(node.textContent ?? '');
    if (!(node instanceof Element)) return '';
    const inner = Array.from(node.childNodes).map(walk).join('');
    const tag = node.tagName.toLowerCase();
    if (tag === 'b' || tag === 'strong') return `<b>${inner}</b>`;
    if (tag === 'code') return `<code>${inner}</code>`;
    if (tag === 'a') {
      const href = node.getAttribute('href') ?? '';
      return /^https?:\/\//i.test(href)
        ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${inner}</a>`
        : inner;
    }
    return inner;
  };
  return walk(doc.body.firstChild as Node);
}

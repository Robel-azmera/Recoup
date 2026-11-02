import { useEffect, useState } from 'react';

import { formatAbsolute, formatRelative } from '../lib/format';

/** Relative time ("3 min ago") with the absolute timestamp on hover. Re-renders every 30s. */
export function RelativeTime({ value, className }: { value: string; className?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);
  return (
    <time dateTime={value} title={formatAbsolute(value)} className={className}>
      {formatRelative(value)}
    </time>
  );
}

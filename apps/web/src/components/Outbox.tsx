import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarDays, Mail, MessageCircle, Send } from 'lucide-react';
import { toast } from 'sonner';

import { usePollInterval } from '../hooks/useLive';
import { ApiError, getOutbox, pressTelegramButton, type OutboxItem } from '../lib/api';
import { formatAbsolute } from '../lib/format';
import { sanitizeTelegramHtml } from '../lib/telegramHtml';
import { EmptyState } from './EmptyState';
import { RelativeTime } from './RelativeTime';

function TelegramMessage({ m }: { m: OutboxItem }) {
  const qc = useQueryClient();
  const press = useMutation({
    mutationFn: (data: string) => pressTelegramButton(m.message_id ?? 0, data),
    onSuccess: (r) => {
      toast.message(`Telegram: ${r.result.replaceAll('_', ' ')}`);
      void qc.invalidateQueries({ queryKey: ['outbox'] });
      void qc.invalidateQueries({ queryKey: ['dispute'] });
      void qc.invalidateQueries({ queryKey: ['disputes'] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Button failed'),
  });
  return (
    <div className="max-w-md rounded-2xl rounded-tl-sm bg-sky-50 p-3 text-sm dark:bg-sky-500/10">
      <div
        className="whitespace-pre-line leading-relaxed [&_a]:text-sky-700 [&_a]:underline [&_code]:font-mono [&_code]:text-xs dark:[&_a]:text-sky-300"
        dangerouslySetInnerHTML={{ __html: sanitizeTelegramHtml(m.text ?? '') }}
      />
      {m.buttons.length > 0 && (
        <div className="mt-2 space-y-1.5">
          {m.buttons.map((row, i) => (
            <div key={i} className="flex gap-1.5">
              {row.map((b) =>
                b.url ? (
                  <a
                    key={b.text}
                    href={b.url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn-secondary flex-1 py-1.5 text-xs"
                  >
                    {b.text}
                  </a>
                ) : (
                  <button
                    key={b.text}
                    type="button"
                    className="btn-secondary flex-1 py-1.5 text-xs"
                    disabled={press.isPending}
                    onClick={() => b.callback_data && press.mutate(b.callback_data)}
                  >
                    {b.text}
                  </button>
                ),
              )}
            </div>
          ))}
        </div>
      )}
      <div className="mt-1 text-right text-[10px] text-slate-500">
        {m.edited_at ? 'edited ' : ''}
        <RelativeTime value={m.edited_at ?? m.sent_at} />
      </div>
    </div>
  );
}

const ICON = { telegram: MessageCircle, calendar: CalendarDays, email: Mail } as const;

/** What the mock Telegram / Calendar / email clients would have sent. Buttons are live. */
export function Outbox() {
  const refetchInterval = usePollInterval();
  const q = useQuery({
    queryKey: ['outbox'],
    queryFn: getOutbox,
    refetchInterval: refetchInterval || 8000,
  });

  return (
    <section className="card flex min-h-0 flex-col" aria-labelledby="outbox-heading">
      <header className="border-b border-slate-200 px-4 py-3 dark:border-slate-800">
        <h2 id="outbox-heading" className="flex items-center gap-2 text-sm font-semibold">
          <Send className="h-4 w-4 text-brand-600" aria-hidden /> Notification outbox (mock)
        </h2>
        <p className="text-xs text-slate-500">
          Without keys, Telegram, Calendar and email go here. The Telegram buttons work and go
          through the same policy gate as the real bot.
        </p>
      </header>
      {q.isLoading ? (
        <div className="space-y-2 p-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton h-16" />
          ))}
        </div>
      ) : !q.data?.length ? (
        <EmptyState icon={Send} title="Nothing sent yet">
          Create a disputed charge in the Demo Console: the alert and calendar event appear here.
        </EmptyState>
      ) : (
        <ul className="max-h-[36rem] space-y-3 overflow-y-auto p-4">
          {q.data.map((m) => {
            const Icon = ICON[m.channel as keyof typeof ICON] ?? Send;
            return (
              <li key={m.id} className="flex gap-2">
                <Icon className="mt-1 h-4 w-4 shrink-0 text-slate-400" aria-label={m.channel} />
                {m.channel === 'telegram' ? (
                  <TelegramMessage m={m} />
                ) : m.channel === 'calendar' ? (
                  <div className="text-sm">
                    <div className="font-medium">{m.summary}</div>
                    <div className="text-xs text-slate-500">
                      Calendar event at {m.start && formatAbsolute(m.start)}
                    </div>
                  </div>
                ) : (
                  <div className="min-w-0 text-sm">
                    <div className="font-medium">{m.subject}</div>
                    <div className="truncate text-xs text-slate-500">
                      to {m.to} · <RelativeTime value={m.sent_at} />
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

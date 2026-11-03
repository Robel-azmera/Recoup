import type { DraftSentence, EvidenceDraft } from '@recoup/shared';
import clsx from 'clsx';
import { AlertCircle, Pencil, UserRound } from 'lucide-react';
import { useId, useMemo } from 'react';

import { EVIDENCE_FIELD_LABEL, EVIDENCE_FIELD_ORDER } from '../../lib/labels';

type Source = EvidenceDraft['sources'][string];

function preview(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'object') {
    const o = value as Record<string, unknown>;
    if ('line1' in o) return [o.line1, o.city, o.state, o.postal_code].filter(Boolean).join(', ');
    return JSON.stringify(value);
  }
  return String(value);
}

/** Citation chip with an accessible hover/focus card showing the source record. */
export function Citation({ n, source, id }: { n: number; source?: Source; id: string }) {
  const tipId = useId();
  return (
    <span className="group relative inline-block align-baseline">
      <button
        type="button"
        aria-describedby={tipId}
        className="mx-0.5 rounded bg-brand-50 px-1 text-[10px] font-semibold text-brand-700 hover:bg-brand-100 dark:bg-brand-500/15 dark:text-brand-100"
      >
        {n}
      </button>
      <span
        id={tipId}
        role="tooltip"
        className="absolute bottom-full left-0 z-20 mb-1 hidden w-64 max-w-[80vw] rounded-lg border border-slate-200 bg-white p-3 text-left text-xs shadow-lg group-focus-within:block group-hover:block sm:left-1/2 sm:w-72 sm:-translate-x-1/2 dark:border-slate-700 dark:bg-slate-900"
      >
        <span className="block font-semibold">{source?.label ?? id}</span>
        <span className="block font-mono text-[10px] text-slate-500">{id}</span>
        {source && (
          <span className="mt-2 block space-y-0.5">
            {Object.entries(source.data)
              .filter(([, v]) => v != null && v !== '')
              .slice(0, 7)
              .map(([k, v]) => (
                <span key={k} className="flex gap-2">
                  <span className="shrink-0 text-slate-500">{k.replaceAll('_', ' ')}</span>
                  <span className="truncate">{preview(v)}</span>
                </span>
              ))}
          </span>
        )}
      </span>
    </span>
  );
}

export function FieldView({
  field,
  sentences,
  sources,
  citationNumber,
}: {
  field: string;
  sentences: DraftSentence[];
  sources: EvidenceDraft['sources'];
  citationNumber: (id: string) => number;
}) {
  return (
    <p className="text-sm leading-relaxed" data-field={field}>
      {sentences.map((s) => {
        const unsupported = !s.supported;
        return (
          <span
            key={s.id}
            className={clsx(
              unsupported &&
                'rounded bg-rose-50 underline decoration-rose-500 decoration-wavy underline-offset-4 dark:bg-rose-500/10',
              s.author === 'user' && 'bg-amber-50/70 dark:bg-amber-500/5',
            )}
            title={unsupported ? `Unsupported: ${s.unsupported_reason ?? 'no source'}` : undefined}
          >
            {s.text}
            {s.author === 'user' && (
              <UserRound
                className="ml-0.5 inline h-3 w-3 text-amber-600"
                aria-label="Written by the analyst"
              />
            )}
            {unsupported && (
              <AlertCircle
                className="ml-0.5 inline h-3.5 w-3.5 text-rose-600"
                aria-label="Unsupported sentence"
              />
            )}
            {s.source_ids.map((id) => (
              <Citation key={id} n={citationNumber(id)} id={id} source={sources[id]} />
            ))}{' '}
          </span>
        );
      })}
    </p>
  );
}

export function useEvidenceFields(draft: EvidenceDraft | null | undefined) {
  return useMemo(() => {
    if (!draft) return { narrative: [], factual: [], citationNumber: () => 0 };
    const byField = new Map<string, DraftSentence[]>();
    for (const s of draft.sentences) byField.set(s.field, [...(byField.get(s.field) ?? []), s]);
    const fields = [...byField.keys()].sort(
      (a, b) => EVIDENCE_FIELD_ORDER.indexOf(a) - EVIDENCE_FIELD_ORDER.indexOf(b),
    );
    const isFactual = (f: string) => byField.get(f)!.every((s) => s.author === 'system');
    const order: string[] = [];
    for (const s of draft.sentences)
      for (const id of s.source_ids) if (!order.includes(id)) order.push(id);
    return {
      narrative: fields
        .filter((f) => !isFactual(f))
        .map((f) => ({ field: f, sentences: byField.get(f)! })),
      factual: fields.filter(isFactual).map((f) => ({ field: f, sentences: byField.get(f)! })),
      citationNumber: (id: string) => order.indexOf(id) + 1,
    };
  }, [draft]);
}

export function EvidenceEditor({
  draft,
  editing,
  values,
  onChange,
}: {
  draft: EvidenceDraft;
  editing: boolean;
  values: Record<string, string>;
  onChange: (field: string, value: string) => void;
}) {
  const { narrative, factual, citationNumber } = useEvidenceFields(draft);
  const text = (field: string) => (draft.evidence as Record<string, string | null>)[field] ?? '';

  return (
    <div className="space-y-5">
      {narrative.map(({ field, sentences }) => (
        <div key={field}>
          <label
            htmlFor={`ev-${field}`}
            className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500"
          >
            {EVIDENCE_FIELD_LABEL[field] ?? field}
            <span className="font-mono normal-case tracking-normal text-slate-400">{field}</span>
          </label>
          {editing ? (
            <textarea
              id={`ev-${field}`}
              value={values[field] ?? text(field)}
              onChange={(e) => onChange(field, e.target.value)}
              rows={Math.min(14, Math.max(3, Math.ceil(text(field).length / 80)))}
              className="w-full rounded-lg border border-slate-300 bg-white p-3 text-sm leading-relaxed dark:border-slate-700 dark:bg-slate-950"
            />
          ) : (
            <FieldView
              field={field}
              sentences={sentences}
              sources={draft.sources}
              citationNumber={citationNumber}
            />
          )}
        </div>
      ))}
      {factual.length > 0 && (
        <details className="group rounded-lg border border-slate-200 dark:border-slate-800">
          <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium">
            Factual fields{' '}
            <span className="text-slate-500">({factual.length}, copied from records)</span>
          </summary>
          <dl className="grid gap-x-4 gap-y-2 border-t border-slate-200 p-3 text-sm sm:grid-cols-[10rem_1fr] dark:border-slate-800">
            {factual.map(({ field, sentences }) => (
              <div key={field} className="contents">
                <dt className="text-slate-500">{EVIDENCE_FIELD_LABEL[field] ?? field}</dt>
                <dd className="min-w-0 break-words">
                  {sentences.map((s) => s.text).join(' ')}
                  {sentences[0]?.source_ids.map((id) => (
                    <Citation key={id} n={citationNumber(id)} id={id} source={draft.sources[id]} />
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {!editing && (
        <p className="flex items-center gap-1.5 text-xs text-slate-500">
          <Pencil className="h-3 w-3" aria-hidden /> Numbers are citations: hover or focus to see
          the source record.
        </p>
      )}
    </div>
  );
}

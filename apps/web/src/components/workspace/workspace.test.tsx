import type { DraftSentence } from '@recoup/shared';
import { render, screen } from '@testing-library/react';

import type { DisputeDetail } from '../../lib/api';
import { FieldView } from './Evidence';
import { buildTimeline } from './Timeline';

const s = (over: Partial<DraftSentence>): DraftSentence => ({
  id: 's1',
  field: 'uncategorized_text',
  text: 'Delivered on November 1, 2026.',
  source_ids: ['ord_1:fulfilment'],
  author: 'agent',
  unsupported_reason: null,
  supported: true,
  ...over,
});

describe('FieldView', () => {
  it('renders citations and flags unsupported sentences', () => {
    render(
      <FieldView
        field="uncategorized_text"
        sentences={[
          s({}),
          s({
            id: 's2',
            text: 'FedEx tracking FX1 confirms it.',
            source_ids: [],
            supported: false,
            unsupported_reason: "'FX1' is not in the cited source(s)",
          }),
        ]}
        sources={{
          'ord_1:fulfilment': {
            id: 'ord_1:fulfilment',
            kind: 'fulfilment',
            label: 'Fulfilment',
            data: { carrier: 'UPS' },
          },
        }}
        citationNumber={() => 1}
      />,
    );
    expect(screen.getByRole('button', { name: '1' })).toBeInTheDocument();
    expect(screen.getByRole('tooltip')).toHaveTextContent('UPS');
    expect(screen.getByLabelText('Unsupported sentence')).toBeInTheDocument();
    expect(screen.getByTitle(/Unsupported: 'FX1'/)).toBeInTheDocument();
  });
});

describe('buildTimeline', () => {
  it('orders events chronologically and ends with the deadline', () => {
    const detail = {
      dispute: {
        id: 'dp_1',
        created_at: '2026-11-02T10:00:00Z',
        due_by: '2026-11-09T10:00:00Z',
        reason_code: 'fraudulent',
        outcome: null,
        currency: 'usd',
      },
      order: {
        id: 'ord_1',
        created_at: '2026-10-28T10:00:00Z',
        items: [{ sku: 'W', name: 'Watch', quantity: 1, unit_amount: 24900 }],
        total: 24900,
        currency: 'usd',
        digital: false,
        ip: null,
        fulfilment: {
          carrier: 'UPS',
          tracking: 'UP1',
          shipped_at: '2026-10-29T10:00:00Z',
          delivered_at: '2026-10-31T10:00:00Z',
          proof_url: null,
          signed_by: 'SARA',
          delivered_to_billing_address: true,
        },
      },
      messages: [
        {
          id: 'm1',
          order_id: 'ord_1',
          direction: 'inbound',
          channel: 'email',
          body: 'Love it',
          sent_at: '2026-11-01T10:00:00Z',
        },
      ],
      actions: [],
      charge: null,
    } as unknown as DisputeDetail;
    const titles = buildTimeline(detail).map((e) => e.title);
    expect(titles).toEqual([
      'Order ord_1 placed',
      'Shipped with UPS',
      'Delivered',
      'Customer (email)',
      'Dispute opened · fraudulent',
      'Response due',
    ]);
  });
});

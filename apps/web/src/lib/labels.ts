import type { DisputeCategory, DisputeStatus } from '@recoup/shared';

export const CATEGORY_LABEL: Record<DisputeCategory, string> = {
  fraudulent: 'Fraudulent',
  product_not_received: 'Not received',
  product_unacceptable: 'Not as described',
  duplicate: 'Duplicate',
  subscription_canceled: 'Subscription cancelled',
  credit_not_processed: 'Credit not processed',
  general: 'General / inquiry',
};

export const STATUS_LABEL: Record<DisputeStatus, string> = {
  needs_response: 'Needs response',
  drafting: 'Agent drafting',
  awaiting_approval: 'Awaiting approval',
  submitted: 'Submitted',
  accepted: 'Accepted',
  won: 'Won',
  lost: 'Lost',
};

export const OPEN_STATUSES: DisputeStatus[] = ['needs_response', 'drafting', 'awaiting_approval'];

export const EVIDENCE_FIELD_LABEL: Record<string, string> = {
  uncategorized_text: 'Rebuttal (additional information)',
  product_description: 'Product description',
  access_activity_log: 'Access / activity log',
  cancellation_rebuttal: 'Cancellation rebuttal',
  refund_refusal_explanation: 'Refund refusal explanation',
  duplicate_charge_explanation: 'Duplicate charge explanation',
  customer_name: 'Customer name',
  customer_email_address: 'Customer email',
  customer_purchase_ip: 'Purchase IP',
  billing_address: 'Billing address',
  shipping_address: 'Shipping address',
  shipping_carrier: 'Shipping carrier',
  shipping_tracking_number: 'Tracking number',
  shipping_date: 'Shipping date',
  service_date: 'Service date',
  refund_policy_disclosure: 'Refund policy',
  cancellation_policy_disclosure: 'Cancellation policy',
  duplicate_charge_id: 'Duplicate charge id',
};

/** Narrative fields first (written by the agent), then the factual ones copied from records. */
export const EVIDENCE_FIELD_ORDER = Object.keys(EVIDENCE_FIELD_LABEL);

export const ACTION_LABEL: Record<string, string> = {
  submit_dispute_evidence: 'Submit evidence to Stripe',
  accept_dispute: 'Accept the dispute (close as lost)',
  refund_charge: 'Refund the charge',
  create_calendar_event: 'Create calendar event',
  send_email: 'Send email',
  send_telegram: 'Send Telegram message',
};

export const CHECK_LABEL: Record<string, string> = {
  payload_integrity: 'Payload matches its hash',
  dispute_exists: 'Dispute exists',
  test_mode: 'Test mode',
  state_transition: 'Dispute can still be responded to',
  before_deadline: 'Before the response deadline',
  grounded: 'Every sentence is supported by a source',
  has_rebuttal: 'Rebuttal text present',
  payload_matches_draft: 'Matches the current draft',
  amount_matches: 'Amount matches',
  charge_exists: 'Charge exists',
  positive_amount: 'Positive amount',
  within_remaining: 'Within the refundable amount',
  under_ceiling: 'Under the refund ceiling',
  not_disputed: 'Charge is not disputed',
};

import type { DemoEvent, DemoSession, TransferTimelineEvent } from "../api/types";

export type UnifiedEvent = {
  key: string;
  timestamp: string;
  source: string;
  action: string;
  status: string;
  detail: string;
  eventId: string | null;
  requestId: string | null;
  traceId: string | null;
  sequence: number;
  attempt: number | null;
};

export function formatMoney(amountMinor = 0, currency = "INR") {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(amountMinor / 100);
}

export function formatTime(timestamp?: string | null) {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hour12: false,
  }).format(date);
}

export function formatDate(timestamp?: string | null) {
  if (!timestamp) return "—";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function shortId(value?: string | null, length = 12) {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function channelLabel(channel?: string) {
  return (
    {
      UPI: "UPI",
      DEBIT_CARD: "Debit card",
      NET_BANKING: "Net banking",
    }[channel ?? ""] ?? channel ?? "Not recorded"
  );
}

export function stateLabel(state?: string) {
  return (
    {
      PREPARED: "READY TO EXECUTE",
      FAULT_ARMED: "CONTROLLED FAULT ARMED",
      AWAITING_REVIEW: "WAITING FOR ENGINEER",
      APPROVED: "RECOVERY APPROVED",
      RECOVERED: "RECOVERY VERIFIED",
      COMPLETED: "PAYMENT COMPLETED",
    }[state ?? ""] ?? state ?? "NOT STARTED"
  );
}

export function fundsLabel(status?: string) {
  return (
    {
      COMMITTED: "MONEY MOVED ONCE",
      UNKNOWN: "MONEY OUTCOME NEEDS CHECKING",
      NOT_COMMITTED: "MONEY NOT MOVED",
      NOT_ATTEMPTED: "MONEY MOVEMENT NOT STARTED",
    }[status ?? ""] ?? status ?? "NOT DETERMINED"
  );
}

export function toneForStatus(status?: string) {
  const value = (status ?? "").toUpperCase();
  if (["FAILED", "ERROR"].some((item) => value.includes(item))) return "danger";
  if (["REVIEW", "UNKNOWN", "PAUSED", "APPROVED"].some((item) => value.includes(item))) {
    return "warning";
  }
  if (["SUCCEEDED", "COMPLETED", "VERIFIED", "READY"].some((item) => value.includes(item))) {
    return "success";
  }
  return "neutral";
}

export function serviceLabel(service?: string) {
  return (
    {
      authentication: "Authentication",
      auth: "Authentication",
      transaction: "Transaction",
      payment: "Payment",
      account: "Account",
      ledger: "Ledger",
      gateway: "API Gateway",
      orchestration: "Demo control",
    }[service?.toLowerCase() ?? ""] ?? service ?? "System"
  );
}

export function stepMeaning(step: string, detail: string) {
  const meanings: Record<string, string> = {
    session_prepared: "The case was saved, but no fault or transfer had run yet.",
    fault_armed: "A bounded fault was activated only for this demo session.",
    transfer_created: "The transfer identity and duplicate-protection key were stored.",
    payment_execution: "Transaction asked Payment to coordinate the money movement.",
    account_commit: "Account attempted the protected sender debit and recipient credit.",
    ledger_posting: "Ledger attempted to save the permanent debit and credit record.",
    transfer_completed: "All required financial evidence was confirmed.",
    transfer_failed: "The workflow stopped and preserved the first failure for review.",
    transfer_executed: "The real transfer finished or entered engineer review.",
    case_analyzed: "Evidence, model status and grounded explanation were joined.",
    recovery_approved: "A human authorized recovery of this same transfer.",
    recovery_verified: "Balances and the paired financial record were verified after recovery.",
  };
  return meanings[step] ?? detail;
}

export function unifyEvents(
  sessionEvents: DemoEvent[] = [],
  timeline: TransferTimelineEvent[] = [],
): UnifiedEvent[] {
  const orchestration = sessionEvents.map((event) => ({
    key: `session-${event.sequence}-${event.timestamp}`,
    timestamp: event.timestamp,
    source: "Demo control",
    action: event.action,
    status: event.status,
    detail: event.detail,
    eventId: null,
    requestId: null,
    traceId: null,
    sequence: event.sequence,
    attempt: null,
  }));
  const persisted = timeline.map((event) => ({
    key: event.event_id,
    timestamp: event.timestamp,
    source: serviceLabel(event.service),
    action: event.step,
    status: event.status,
    detail: event.detail,
    eventId: event.event_id,
    requestId: event.request_id,
    traceId: event.trace_id,
    sequence: event.sequence,
    attempt: event.attempt,
  }));
  return [...orchestration, ...persisted].sort((left, right) => {
    const timestampDifference = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
    return timestampDifference || left.sequence - right.sequence;
  });
}

export function latestEvidence(session: DemoSession | null) {
  if (!session) return null;
  return (
    session.snapshots.evidence_after_recovery ??
    session.snapshots.latest_evidence ??
    session.snapshots.evidence_after_execution ??
    null
  );
}

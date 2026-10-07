import type { DemoCase, TelemetryEvent } from "../api/types";
import { formatLabel } from "./format";

export function eventFailed(event: TelemetryEvent) {
  return (
    event.success === false ||
    event.level === "ERROR" ||
    event.level === "CRITICAL" ||
    Number(event.status_code) >= 400
  );
}

function eventTime(value?: string | null) {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

export function transactionEndedAt(item: DemoCase) {
  return eventTime(item.transaction_result?.ended_at);
}

export function isLateCaseEvent(item: DemoCase, event: TelemetryEvent) {
  const endedAt = transactionEndedAt(item);
  const timestamp = eventTime(event.timestamp);
  return endedAt !== null && timestamp !== null && timestamp > endedAt;
}

export function explainEvent(item: DemoCase, event: TelemetryEvent) {
  const late = isLateCaseEvent(item, event);
  const status = event.status_code;
  if (event.event_type === "http_request_completed") {
    if (late) {
      return `Late HTTP ${status ?? "response"} after the gateway had already answered the customer`;
    }
    if (typeof status === "number" && status >= 400) {
      return `HTTP ${status} error response sent; the business transaction did not complete`;
    }
    return `HTTP ${status ?? "response"} sent during the customer request`;
  }
  if (event.event_type === "downstream_request_failed") {
    return event.downstream_service
      ? `${formatLabel(event.service)} could not complete its call to ${formatLabel(event.downstream_service)}`
      : "A downstream dependency call failed before a usable response arrived";
  }
  if (event.event_type === "transaction_flow_failed") {
    return "The gateway ended the customer transaction as failed";
  }
  return event.message;
}

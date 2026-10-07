import { describe, expect, it } from "vitest";
import type { DemoCase, TelemetryEvent } from "../api/types";
import { explainEvent, isLateCaseEvent } from "./caseEvidence";

const item = {
  transaction_result: {
    ended_at: "2026-09-27T12:00:05.000Z",
  },
} as DemoCase;

function event(overrides: Partial<TelemetryEvent>): TelemetryEvent {
  return {
    timestamp: "2026-09-27T12:00:04.000Z",
    service: "gateway",
    level: "INFO",
    event_type: "http_request_completed",
    message: "HTTP request completed",
    ...overrides,
  };
}

describe("case evidence interpretation", () => {
  it("does not describe a failed HTTP exchange as business success", () => {
    const failed = event({ status_code: 503, level: "ERROR" });

    expect(explainEvent(item, failed)).toContain("business transaction did not complete");
  });

  it("separates a late dependency response from recovery", () => {
    const late = event({
      timestamp: "2026-09-27T12:00:08.000Z",
      service: "authentication",
      status_code: 200,
    });

    expect(isLateCaseEvent(item, late)).toBe(true);
    expect(explainEvent(item, late)).toContain("gateway had already answered");
  });
});

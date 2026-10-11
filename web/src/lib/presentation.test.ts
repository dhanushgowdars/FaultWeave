import { describe, expect, it } from "vitest";

import { fundsLabel, stateLabel, unifyEvents } from "./presentation";

describe("evidence presentation", () => {
  it("uses plain-language financial states without claiming unknown money moved", () => {
    expect(fundsLabel("COMMITTED")).toBe("MONEY MOVED ONCE");
    expect(fundsLabel("UNKNOWN")).toBe("MONEY OUTCOME NEEDS CHECKING");
    expect(stateLabel("AWAITING_REVIEW")).toBe("WAITING FOR ENGINEER");
  });

  it("orders orchestration and transfer evidence by timestamp", () => {
    const events = unifyEvents(
      [
        {
          sequence: 1,
          timestamp: "2026-10-10T10:00:00.000Z",
          action: "session_prepared",
          status: "READY",
          detail: "Prepared",
        },
      ],
      [
        {
          event_id: "event-1",
          sequence: 1,
          attempt: 1,
          timestamp: "2026-10-10T10:00:01.000Z",
          service: "transaction",
          step: "transfer_created",
          status: "SUCCEEDED",
          detail: "Stored",
          request_id: "request-1",
          trace_id: "trace-1",
          run_id: "run-1",
        },
      ],
    );
    expect(events.map((event) => event.action)).toEqual(["session_prepared", "transfer_created"]);
    expect(events[1].eventId).toBe("event-1");
  });
});

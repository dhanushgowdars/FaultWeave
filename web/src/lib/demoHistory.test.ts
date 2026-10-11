import { describe, expect, it } from "vitest";

import type { DemoSession } from "../api/types";
import {
  CASE_IDS_KEY,
  clearCurrentDemo,
  currentDemoSessions,
  readCurrentCaseIds,
  rememberCurrentCase,
} from "./demoHistory";

function session(sessionId: string) {
  return { session_id: sessionId } as DemoSession;
}

describe("current engineer-session case history", () => {
  it("starts empty and never imports unrelated persisted backend sessions", () => {
    sessionStorage.clear();
    const backendHistory = [session("old-1"), session("old-2")];
    expect(readCurrentCaseIds()).toEqual([]);
    expect(currentDemoSessions(backendHistory, readCurrentCaseIds())).toEqual([]);
  });

  it("numbers cases in the order this browser session completed them", () => {
    sessionStorage.clear();
    expect(rememberCurrentCase("demo-one")).toEqual(["demo-one"]);
    expect(rememberCurrentCase("demo-two")).toEqual(["demo-one", "demo-two"]);
    expect(rememberCurrentCase("demo-one")).toEqual(["demo-one", "demo-two"]);
    const ordered = currentDemoSessions(
      [session("unrelated"), session("demo-two"), session("demo-one")],
      readCurrentCaseIds(),
    );
    expect(ordered.map((item) => item.session_id)).toEqual(["demo-one", "demo-two"]);
    clearCurrentDemo();
    expect(sessionStorage.getItem(CASE_IDS_KEY)).toBeNull();
  });
});

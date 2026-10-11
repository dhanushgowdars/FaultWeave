import type { DemoSession } from "../api/types";

export const CASE_IDS_KEY = "faultweave.demo.current-case-ids";
export const ACTIVE_CASE_KEY = "faultweave.demo.current-case";

export function readCurrentCaseIds(storage: Storage = sessionStorage): string[] {
  try {
    const parsed = JSON.parse(storage.getItem(CASE_IDS_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === "string");
  } catch {
    return [];
  }
}

export function rememberCurrentCase(sessionId: string, storage: Storage = sessionStorage) {
  const existing = readCurrentCaseIds(storage);
  const next = existing.includes(sessionId) ? existing : [...existing, sessionId];
  storage.setItem(CASE_IDS_KEY, JSON.stringify(next));
  storage.setItem(ACTIVE_CASE_KEY, sessionId);
  return next;
}

export function currentDemoSessions(allSessions: DemoSession[], caseIds: string[]) {
  const byId = new Map(allSessions.map((session) => [session.session_id, session]));
  return caseIds.flatMap((sessionId) => {
    const session = byId.get(sessionId);
    return session ? [session] : [];
  });
}

export function clearCurrentDemo(storage: Storage = sessionStorage) {
  storage.removeItem(CASE_IDS_KEY);
  storage.removeItem(ACTIVE_CASE_KEY);
}

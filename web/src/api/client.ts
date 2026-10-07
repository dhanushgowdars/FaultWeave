import type {
  CurrentIncident,
  DemoCase,
  DemoCaseList,
  DemoCatalog,
  DemoStatus,
  Incident,
  IncidentList,
  LiveInference,
  Readiness,
  TelemetryEvidence,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message =
      body?.detail ?? body?.reason ?? `Request failed (${response.status})`;
    throw new ApiError(String(message), response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ service: string; status: string }>("/health/ready"),
  readiness: () => request<Readiness>("/api/v1/intelligence/ready"),
  currentIncident: () =>
    request<CurrentIncident>("/api/v1/intelligence/incidents/current"),
  incidents: () => request<IncidentList>("/api/v1/intelligence/incidents"),
  incident: (id: string) =>
    request<Incident>(
      `/api/v1/intelligence/incidents/${encodeURIComponent(id)}`,
    ),
  liveInference: (seconds: 10 | 60) =>
    request<LiveInference>(`/api/v1/intelligence/live/infer/${seconds}`),
  evaluate: () =>
    request<Record<string, unknown>>("/api/v1/intelligence/live/evaluate", {
      method: "POST",
    }),
  telemetry: (params: URLSearchParams) =>
    request<TelemetryEvidence>(
      `/api/v1/intelligence/live/telemetry/recent?${params.toString()}`,
    ),
  demoCatalog: () => request<DemoCatalog>("/demo-control/catalog"),
  demoStatus: () => request<DemoStatus>("/demo-control/status"),
  demoCases: () => request<DemoCaseList>("/demo-control/cases"),
  demoCase: (caseId: string) =>
    request<DemoCase>(`/demo-control/cases/${encodeURIComponent(caseId)}`),
  startDemo: (payload: { fault_id: string }) =>
    request<DemoStatus>("/demo-control/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        confirmation: "INJECT_CONTROLLED_FAULT",
      }),
    }),
  runDemoTransaction: (caseId: string) =>
    request<DemoStatus>(
      `/demo-control/cases/${encodeURIComponent(caseId)}/transactions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "SEND_TEST_TRANSACTION" }),
      },
    ),
  approveRemediation: (caseId: string, approvedBy: string) =>
    request<DemoStatus>(
      `/demo-control/cases/${encodeURIComponent(caseId)}/remediate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmation: "APPROVE_REMEDIATION",
          approved_by: approvedBy,
        }),
      },
    ),
  verifyDemoRecovery: (caseId: string) =>
    request<DemoStatus>(
      `/demo-control/cases/${encodeURIComponent(caseId)}/verify`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "VERIFY_RECOVERY" }),
      },
    ),
};

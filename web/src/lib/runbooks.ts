export interface IncidentExplanation {
  happened: string;
  mechanism: string;
  checks: string[];
}

const RUNBOOKS: Record<string, IncidentExplanation> = {
  DATABASE_HIGH_LATENCY: {
    happened: "Database-dependent requests became slower than the learned healthy envelope.",
    mechanism: "Likely database query delay, resource pressure, or a slow dependency path.",
    checks: ["Inspect slow-query and PostgreSQL resource metrics", "Compare database latency with the baseline window", "Check the top trace IDs before changing capacity"],
  },
  DATABASE_UNAVAILABLE: {
    happened: "Services could not establish a usable PostgreSQL connection.",
    mechanism: "Likely database process, network reachability, or connection-endpoint failure.",
    checks: ["Confirm PostgreSQL health and reachability", "Inspect connection errors in the affected service", "Restore access, then verify a normal transaction"],
  },
  SERVICE_UNAVAILABLE: {
    happened: "A required downstream service stopped returning a usable response.",
    mechanism: "Likely unhealthy service instance, stopped process, or unavailable endpoint.",
    checks: ["Check the probable-origin service health", "Inspect its logs using the displayed trace IDs", "Restore the service and verify the complete dependency path"],
  },
  DOWNSTREAM_TIMEOUT: {
    happened: "An upstream service waited longer than its allowed downstream timeout.",
    mechanism: "Likely downstream slowness, saturation, or a delayed network/dependency path.",
    checks: ["Compare upstream and downstream latency", "Inspect downstream saturation and pool waits", "Reduce pressure or repair the slow dependency before retrying"],
  },
  AUTHENTICATION_FAILURE_BURST: {
    happened: "Authentication failures increased sharply within the observation window.",
    mechanism: "Likely invalid credentials, token/configuration mismatch, or an abnormal login burst.",
    checks: ["Inspect authentication outcomes without exposing secrets", "Verify service-token and credential configuration", "Rate-limit abusive traffic while preserving audit evidence"],
  },
  HIGH_LOAD: {
    happened: "Request volume and tail latency rose beyond the calibrated healthy range.",
    mechanism: "Likely workload saturation rather than one isolated request failure.",
    checks: ["Inspect request rate and p95/p99 latency", "Find the first saturated service in the dependency path", "Throttle or scale only after confirming the bottleneck"],
  },
  CONNECTION_POOL_EXHAUSTION: {
    happened: "Requests waited for or failed to obtain database connections.",
    mechanism: "Likely concurrency beyond pool capacity, long-held connections, or a connection leak.",
    checks: ["Inspect active, idle, and waiting connections", "Check long-running transactions", "Fix leaks or bound concurrency before increasing pool size"],
  },
  DATABASE_LOCK_CONTENTION: {
    happened: "Concurrent transactions were delayed by competing database locks.",
    mechanism: "Likely long transactions or conflicting updates on shared records.",
    checks: ["Inspect PostgreSQL lock waiters and blockers", "Identify the longest transaction", "Shorten or serialize the conflicting operation safely"],
  },
  DOWNSTREAM_ERROR_BURST: {
    happened: "A downstream dependency returned an elevated burst of error responses.",
    mechanism: "Likely application failure inside the dependency or an invalid dependency response.",
    checks: ["Inspect 5xx events at the probable origin", "Follow request and trace IDs across services", "Repair or roll back the failing dependency, then verify recovery"],
  },
};

const UNKNOWN: IncidentExplanation = {
  happened: "The observable behavior is abnormal but does not fit accepted support for a known class.",
  mechanism: "The exact mechanism is intentionally left unclaimed; the model rejected forced classification.",
  checks: ["Preserve the raw evidence and trace IDs", "Inspect the top localization candidates", "Escalate for investigation instead of applying a class-specific fix"],
};

export function incidentExplanation(label: string | null | undefined, accepted: boolean | null | undefined) {
  if (accepted === false || !label) return UNKNOWN;
  return RUNBOOKS[label] ?? UNKNOWN;
}

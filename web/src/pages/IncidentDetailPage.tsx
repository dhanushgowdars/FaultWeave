import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Database } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { GuidedDemoWorkspace } from "../components/GuidedDemoWorkspace";
import { Panel, QueryState, StatusPill } from "../components/ui";
import {
  eventFailed,
  explainEvent,
  isLateCaseEvent,
} from "../lib/caseEvidence";
import { formatLabel, formatTime } from "../lib/format";

const tabs = ["Summary", "Chronology", "Evidence logs", "Raw case"] as const;

export function IncidentDetailPage() {
  const queryClient = useQueryClient();
  const { caseId = "" } = useParams();
  const [tab, setTab] = useState<(typeof tabs)[number]>("Summary");
  const result = useQuery({
    queryKey: ["demo-case", caseId],
    queryFn: () => api.demoCase(caseId),
    enabled: Boolean(caseId),
    refetchInterval: 2_000,
    retry: false,
  });
  const item = result.data;
  const transact = useMutation({
    mutationFn: () => api.runDemoTransaction(caseId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["demo-case", caseId] }),
  });
  const remediate = useMutation({
    mutationFn: () => api.approveRemediation(caseId, "Demo engineer"),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["demo-case", caseId] }),
  });
  const verify = useMutation({
    mutationFn: () => api.verifyDemoRecovery(caseId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["demo-case", caseId] }),
  });
  const busy = transact.isPending || remediate.isPending || verify.isPending;
  const actionError = transact.error ?? remediate.error ?? verify.error;

  function handleAction(action: "transaction" | "remediate" | "verify") {
    if (action === "transaction") transact.mutate();
    if (action === "remediate") remediate.mutate();
    if (action === "verify") verify.mutate();
  }

  if (result.isLoading)
    return (
      <div className="page-container">
        <QueryState
          kind="loading"
          title="Loading case evidence"
          message="Reading the persisted transaction record."
        />
      </div>
    );
  if (result.isError || !item)
    return (
      <div className="page-container">
        <QueryState
          kind="error"
          title="Case not found"
          message="Start the demo controller or verify the case identifier."
        />
      </div>
    );

  return (
    <div className="page-container page-container--wide">
      <Link className="back-link" to="/cases">
        <ArrowLeft size={16} /> Back to saved cases
      </Link>
      <header className="incident-header">
        <div>
          <div className="incident-header__meta">
            <StatusPill
              tone={
                item.status === "FAILED"
                  ? "danger"
                  : item.status === "RESOLVED"
                    ? "success"
                    : "warning"
              }
            >
              {formatLabel(item.status)}
            </StatusPill>
            <span>{item.case_id}</span>
          </div>
          <h1>{item.scenario.title}</h1>
          <p>{item.scenario.customer_impact}</p>
        </div>
        <button
          className="button button--secondary"
          onClick={() => void navigator.clipboard.writeText(item.case_id)}
        >
          <Copy size={16} /> Copy case ID
        </button>
      </header>
      <div className="case-identity case-identity--detail">
        <div>
          <small>Request ID</small>
          <strong className="mono">{item.request_id}</strong>
        </div>
        <div>
          <small>Trace ID</small>
          <strong className="mono">{item.trace_id}</strong>
        </div>
        <div>
          <small>Run ID</small>
          <strong className="mono">{item.run_id}</strong>
        </div>
        <div>
          <small>Evidence store</small>
          <strong>SQLite case database</strong>
        </div>
      </div>
      <nav className="detail-tabs" aria-label="Case detail sections">
        {tabs.map((name) => (
          <button
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
            key={name}
          >
            {name}
          </button>
        ))}
      </nav>

      {tab === "Summary" && (
        <div className="detail-stack">
          <div className="case-summary-grid">
            <article>
              <small>Customer outcome</small>
              <strong>
                {item.transaction_result
                  ? `HTTP ${item.transaction_result.status_code ?? "transport failure"}`
                  : "Pending"}
              </strong>
              <span>
                {item.transaction_result
                  ? `${item.transaction_result.latency_ms.toFixed(1)} ms observed latency`
                  : "Transaction is running"}
              </span>
            </article>
            <article className="is-danger">
              <small>Controlled cause</small>
              <strong>{item.scenario.cause_component}</strong>
              <span>{item.scenario.cause_action}</span>
            </article>
            <article>
              <small>First observed failure</small>
              <strong>
                {item.first_failure
                  ? `${formatLabel(item.first_failure.service)}${item.first_failure.downstream_service ? ` → ${formatLabel(item.first_failure.downstream_service)}` : ""}`
                  : "No failed event"}
              </strong>
              <span>
                {item.first_failure
                  ? explainEvent(item, item.first_failure)
                  : "The request completed without a failed event."}
              </span>
            </article>
            <article>
              <small>Separate recovery request</small>
              <strong>
                {item.recovery_verified && item.recovery_result?.succeeded
                  ? `HTTP ${item.recovery_result.status_code} verified`
                  : "Pending or failed"}
              </strong>
              <span>
                {item.recovery_result?.request_id ??
                  "A recovery request has not been issued yet."}
              </span>
            </article>
          </div>
          <GuidedDemoWorkspace
            item={item}
            busy={busy}
            onAction={handleAction}
          />
          {actionError && (
            <div className="inline-error">{actionError.message}</div>
          )}
        </div>
      )}

      {tab === "Chronology" && (
        <Panel
          title="Exact case chronology"
          subtitle="Controller lifecycle and request evidence are separated so cleanup cannot be mistaken for customer success"
        >
          <div className="chronology">
            <h3>Controller lifecycle</h3>
            <ol>
              {item.timeline.map((event) => (
                <li
                  className={event.state === "FAILED" ? "is-failed" : ""}
                  key={event.sequence}
                >
                  <time>{formatTime(event.timestamp)}</time>
                  <span>
                    <strong>{event.title}</strong>
                    <small>
                      {formatLabel(event.service)} · {event.detail}
                    </small>
                  </span>
                </li>
              ))}
            </ol>
            <h3>Original customer request</h3>
            <ol>
              {item.evidence.map((event, index) => (
                <li
                  className={`${eventFailed(event) ? "is-failed" : ""} ${isLateCaseEvent(item, event) ? "is-late" : ""}`}
                  key={`${event.timestamp}-${index}`}
                >
                  <time>{formatTime(event.timestamp)}</time>
                  <span>
                    <strong>{explainEvent(item, event)}</strong>
                    <small>
                      {formatLabel(event.service)} ·{" "}
                      {formatLabel(event.event_type)}
                      {isLateCaseEvent(item, event)
                        ? " · LATE AFTER CUSTOMER RESPONSE"
                        : ""}
                    </small>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </Panel>
      )}

      {tab === "Evidence logs" && (
        <Panel
          title="Structured logs for the original request"
          subtitle="A completed HTTP exchange is not automatically a successful transaction; the interpretation column explains the difference"
          action={
            <StatusPill tone="neutral">
              {item.evidence.length} correlated events
            </StatusPill>
          }
        >
          <div className="log-table-wrap">
            <table className="log-table">
              <thead>
                <tr>
                  <th>Timestamp</th>
                  <th>Phase</th>
                  <th>Service / event</th>
                  <th>Operational meaning</th>
                  <th>Level</th>
                  <th>HTTP</th>
                  <th>Latency</th>
                </tr>
              </thead>
              <tbody>
                {item.evidence.map((event, index) => (
                  <tr
                    className={`${eventFailed(event) ? "log-row--failed" : ""} ${isLateCaseEvent(item, event) ? "log-row--late" : ""}`}
                    key={`${event.timestamp}-${index}`}
                  >
                    <td className="mono">
                      {new Date(event.timestamp).toLocaleString()}
                    </td>
                    <td>
                      <StatusPill
                        tone={
                          isLateCaseEvent(item, event)
                            ? "warning"
                            : eventFailed(event)
                              ? "danger"
                              : "neutral"
                        }
                      >
                        {isLateCaseEvent(item, event)
                          ? "Late"
                          : "Customer request"}
                      </StatusPill>
                    </td>
                    <td>
                      <strong>{formatLabel(event.service)}</strong>
                      <small>{formatLabel(event.event_type)}</small>
                    </td>
                    <td>
                      <strong>{explainEvent(item, event)}</strong>
                      <small>{event.message}</small>
                    </td>
                    <td>
                      <StatusPill
                        tone={eventFailed(event) ? "danger" : "neutral"}
                      >
                        {event.level}
                      </StatusPill>
                    </td>
                    <td>{event.status_code ?? "—"}</td>
                    <td>
                      {event.latency_ms != null
                        ? `${Number(event.latency_ms).toFixed(1)} ms`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!item.evidence.length && (
            <QueryState
              kind="empty"
              title="Evidence not captured yet"
              message="The case is still running or the telemetry bridge is unavailable."
            />
          )}
        </Panel>
      )}

      {tab === "Raw case" && (
        <Panel
          title="Raw persisted case record"
          subtitle="Exact JSON reconstructed from the SQLite document"
          action={
            <button
              className="button button--small"
              onClick={() =>
                void navigator.clipboard.writeText(
                  JSON.stringify(item, null, 2),
                )
              }
            >
              <Copy size={14} /> Copy JSON
            </button>
          }
        >
          <div className="raw-case-note">
            <Database size={18} /> This is the stored case contract—not an
            unfiltered live buffer.
          </div>
          <pre className="raw-json">{JSON.stringify(item, null, 2)}</pre>
        </Panel>
      )}
    </div>
  );
}

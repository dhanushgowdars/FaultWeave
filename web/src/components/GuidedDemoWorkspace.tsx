import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  FileSearch,
  Play,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import type { DemoCase } from "../api/types";
import { eventFailed, explainEvent } from "../lib/caseEvidence";
import { formatLabel, formatTime } from "../lib/format";
import { StatusPill } from "./ui";

const services = [
  ["gateway", "API Gateway", "Accept request"],
  ["authentication", "Authentication", "Verify login"],
  ["account", "Account", "Validate account"],
  ["transaction", "Transaction", "Create transaction record"],
  ["payment", "Payment", "Process payment"],
  ["ledger", "Ledger", "Record entries"],
] as const;

type ActionKind = "transaction" | "remediate" | "verify";

interface GuidedDemoWorkspaceProps {
  item: DemoCase;
  busy: boolean;
  canApprove?: boolean;
  onAction?: (action: ActionKind) => void;
}

function serviceState(item: DemoCase, key: string, index: number) {
  if (item.status === "FAULT_ACTIVE") {
    return key === item.scenario.failure_stage ? "fault" : "waiting";
  }
  if (!item.transaction_result) {
    return key === "gateway" ? "active" : "waiting";
  }
  const firstFailedService =
    item.first_failure?.downstream_service ?? item.first_failure?.service;
  const failedIndex = services.findIndex(([service]) => service === firstFailedService);
  const events = item.evidence.filter((event) => event.service === key);
  if (key === firstFailedService)
    return item.transaction_result.succeeded ? "degraded" : "failed";
  if (events.some(eventFailed)) return "degraded";
  if (events.length) return "observed";
  if (failedIndex >= 0 && index > failedIndex) return "blocked";
  return "waiting";
}

function serviceDetail(item: DemoCase, key: string, index: number) {
  const state = serviceState(item, key, index);
  const evidence = item.evidence.filter((event) => event.service === key);
  if (state === "fault")
    return item.fault_id === "AUTHENTICATION_FAILURE_BURST" || item.fault_id === "HIGH_LOAD"
      ? "Traffic scenario armed; no burst sent yet"
      : "Controlled condition active";
  if (state === "waiting" && item.status === "FAULT_ACTIVE")
    return "No customer request sent";
  if (state === "active") return "Transaction executing";
  if (state === "waiting") return "Waiting for upstream step";
  if (state === "blocked") return "No event captured after failure";
  if (state === "failed") return "First observed failure";
  if (state === "degraded") return "Failure propagated here";
  if (state === "observed") return `${evidence.length} correlated events observed`;
  return "No correlated event captured";
}

function nextAction(
  item: DemoCase,
): { kind: ActionKind; title: string; detail: string } | null {
  if (item.status === "FAULT_ACTIVE") {
    return {
      kind: "transaction",
      title: "Send test transaction",
      detail: `Send one highlighted request and ${Math.max(0, item.scenario.request_count - 1)} supporting requests through the active fault.`,
    };
  }
  if (item.status === "AWAITING_REVIEW") {
    return {
      kind: "remediate",
      title: "Approve remediation",
      detail: "Remove the controlled condition after reviewing the evidence.",
    };
  }
  if (item.status === "READY_TO_VERIFY") {
    return {
      kind: "verify",
      title: "Verify with a new transaction",
      detail: "Use a new request and trace identity to prove recovery.",
    };
  }
  return null;
}

function modelValue(item: DemoCase, path: string[], fallback: string) {
  let value: unknown = item.model_analysis;
  for (const part of path) {
    if (!value || typeof value !== "object") return fallback;
    value = (value as Record<string, unknown>)[part];
  }
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : fallback;
}

export function GuidedDemoWorkspace({
  item,
  busy,
  canApprove = false,
  onAction,
}: GuidedDemoWorkspaceProps) {
  const action = nextAction(item);
  const outcome = item.transaction_result
    ? item.transaction_result.succeeded
      ? `HTTP ${item.transaction_result.status_code} · completed in ${item.transaction_result.latency_ms.toFixed(1)} ms`
      : `HTTP ${item.transaction_result.status_code ?? "transport error"} · failed after ${item.transaction_result.latency_ms.toFixed(1)} ms`
    : "No customer transaction has been sent";
  const firstFailure = item.first_failure
    ? explainEvent(item, item.first_failure)
    : item.transaction_result
      ? "No failed structured event was captured for this request."
      : "Waiting for the original transaction.";

  return (
    <div className="guided-workspace">
      <section className="guided-case-bar">
        <div>
          <small>Active case</small>
          <strong>{item.scenario.title}</strong>
        </div>
        <div>
          <small>Request ID</small>
          <strong className="mono">{item.request_id}</strong>
        </div>
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
      </section>

      <section className="guided-action">
        <div className="guided-action__index">Next</div>
        <div>
          <small>Presenter-controlled workflow</small>
          <h2>
            {action?.title ??
              (item.status === "RESOLVED"
                ? "Case resolved and verified"
                : formatLabel(item.status))}
          </h2>
          <p>
            {action?.detail ??
              (item.status === "RESOLVED"
                ? "The original failed request and the successful recovery request remain separately recorded."
                : "This case has no further automated action.")}
          </p>
          {item.supporting_requests && item.supporting_requests.length > 0 && (
            <p>
              {item.supporting_requests.length} supporting request results are
              stored separately; the service path and logs below belong to the
              highlighted request only.
            </p>
          )}
        </div>
        {action && onAction && (
          <button
            className="button button--primary button--large"
            disabled={busy || (action.kind === "remediate" && !canApprove)}
            onClick={() => onAction(action.kind)}
          >
            {action.kind === "transaction" ? (
              <Play size={18} />
            ) : action.kind === "verify" ? (
              <RotateCcw size={18} />
            ) : (
              <ShieldCheck size={18} />
            )}
            {busy ? "Working…" : action.title}
          </button>
        )}
      </section>

      <div className="guided-main-grid">
        <section className="guided-architecture">
          <header>
            <div>
              <small>Observed path for the highlighted request</small>
              <h2>Original transaction</h2>
            </div>
            <strong>{outcome}</strong>
          </header>
          <div className="guided-service-path">
            {services.map(([key, label, purpose], index) => {
              const state = serviceState(item, key, index);
              return (
                <div className="guided-service-wrap" key={key}>
                  <article className={`guided-service is-${state}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{label}</strong>
                      <small>{purpose}</small>
                      <em>{serviceDetail(item, key, index)}</em>
                    </div>
                  </article>
                  {index < services.length - 1 && <ArrowRight size={20} />}
                </div>
              );
            })}
          </div>
          <div className="guided-fault-callout">
            <AlertTriangle size={20} />
            <div>
              <small>Controlled test condition</small>
              <strong>{item.scenario.cause_component}</strong>
              <p>{item.scenario.cause_action}</p>
            </div>
            <StatusPill
              tone={
                item.status === "FAULT_ACTIVE" ||
                item.status === "AWAITING_REVIEW"
                  ? "danger"
                  : "neutral"
              }
            >
              {item.status === "FAULT_ACTIVE" ||
              item.status === "AWAITING_REVIEW"
                ? "Active"
                : "Recorded"}
            </StatusPill>
          </div>
        </section>

        <aside className="guided-investigation">
          <header>
            <small>FaultWeave investigation</small>
            <h2>What the system concluded</h2>
          </header>
          <dl>
            <div>
              <dt>Customer outcome</dt>
              <dd>
                {item.transaction_result
                  ? item.transaction_result.succeeded
                    ? "Degraded / inspect"
                    : "Abnormal transaction"
                  : "Waiting for evidence"}
              </dd>
            </div>
            <div>
              <dt>Known-fault candidate</dt>
              <dd>
                {formatLabel(
                  modelValue(
                    item,
                    ["classification", "known_class_candidate"],
                    "Not available yet",
                  ),
                )}
              </dd>
            </div>
            <div>
              <dt>Probable origin</dt>
              <dd>
                {formatLabel(
                  modelValue(
                    item,
                    ["localization", "probable_origin"],
                    "Not available for this case",
                  ),
                )}
              </dd>
            </div>
            <div>
              <dt>Severity</dt>
              <dd>
                {formatLabel(
                  modelValue(item, ["severity", "level"], "Not available yet"),
                )}
              </dd>
            </div>
            <div>
              <dt>Evidence stored</dt>
              <dd>{item.evidence.length} correlated events</dd>
            </div>
          </dl>
          <p className="guided-model-note">
            <ShieldCheck size={16} /> {item.model_analysis_note ??
            "The selected fault label is not a model prediction. Model output requires case-linked evidence."}
          </p>
        </aside>
      </div>

      <section className="guided-explanation">
        <header>
          <small>Readable explanation</small>
          <h2>What happened and why</h2>
        </header>
        <div>
          <article>
            <span>01</span>
            <div>
              <strong>Customer outcome</strong>
              <p>{outcome}</p>
            </div>
          </article>
          <article>
            <span>02</span>
            <div>
              <strong>First evidence of failure</strong>
              <p>{firstFailure}</p>
            </div>
          </article>
          <article>
            <span>03</span>
            <div>
              <strong>Proposed engineering action</strong>
              <p>{item.scenario.recovery_action}</p>
            </div>
          </article>
        </div>
      </section>

      {item.recovery_result && (
        <section className="guided-comparison">
          <header>
            <small>Recovery proof</small>
            <h2>Original request versus new verification request</h2>
          </header>
          <div>
            <article className="is-original">
              <strong>Original request</strong>
              <span className="mono">{item.request_id}</span>
              <em>
                {item.transaction_result?.succeeded
                  ? "Completed with the controlled condition"
                  : `Failed · HTTP ${item.transaction_result?.status_code ?? "ERR"}`}
              </em>
            </article>
            <ArrowRight size={24} />
            <article className="is-recovered">
              <strong>Recovery request</strong>
              <span className="mono">{item.recovery_result.request_id}</span>
              <em>
                {item.recovery_result.succeeded
                  ? `Completed · HTTP ${item.recovery_result.status_code}`
                  : "Verification failed"}
              </em>
            </article>
          </div>
        </section>
      )}

      <div className="guided-evidence-grid">
        <section className="guided-timeline">
          <header>
            <small>Controller audit trail</small>
            <h2>Case timeline</h2>
          </header>
          <ol>
            {item.timeline.map((entry) => (
              <li
                key={entry.sequence}
                className={entry.state === "FAILED" ? "is-failed" : ""}
              >
                <time>{formatTime(entry.timestamp)}</time>
                <span>
                  {entry.state === "FAILED" ? (
                    <AlertTriangle size={16} />
                  ) : entry.state === "RUNNING" ? (
                    <CircleDashed size={16} />
                  ) : (
                    <CheckCircle2 size={16} />
                  )}
                </span>
                <div>
                  <strong>{entry.title}</strong>
                  <p>{entry.detail}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
        <section className="guided-logs">
          <header>
            <small>Request-correlated evidence</small>
            <h2>Structured logs</h2>
          </header>
          {item.evidence.length ? (
            <ol>
              {item.evidence.slice(0, 10).map((event, index) => (
                <li
                  key={`${event.timestamp}-${index}`}
                  className={eventFailed(event) ? "is-failed" : ""}
                >
                  <time>{formatTime(event.timestamp)}</time>
                  <span>{formatLabel(event.service)}</span>
                  <div>
                    <strong>{explainEvent(item, event)}</strong>
                    <small>{formatLabel(event.event_type)}</small>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="guided-empty">
              <FileSearch size={25} />
              <strong>No transaction evidence yet</strong>
              <p>
                Injecting a fault does not generate fake logs. Send the test
                transaction when ready.
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

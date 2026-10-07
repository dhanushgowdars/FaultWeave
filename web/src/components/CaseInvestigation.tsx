import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Database,
  FileSearch,
  LockKeyhole,
  RotateCcw,
  ServerCrash,
  ShieldCheck,
} from "lucide-react";
import type { DemoCase } from "../api/types";
import {
  eventFailed,
  explainEvent,
  isLateCaseEvent,
  transactionEndedAt,
} from "../lib/caseEvidence";
import { formatLabel, formatTime } from "../lib/format";

const servicePath = [
  ["gateway", "API Gateway", "Accept request"],
  ["authentication", "Authentication", "Verify login"],
  ["account", "Account", "Validate account"],
  ["transaction", "Transaction", "Create transfer"],
  ["payment", "Payment", "Process payment"],
  ["ledger", "Ledger", "Record entries"],
] as const;

function outcomeText(item: DemoCase) {
  const result = item.transaction_result;
  if (!result) return "Waiting for the customer transaction";
  if (result.succeeded) {
    return `Customer received HTTP ${result.status_code} in ${result.latency_ms.toFixed(1)} ms`;
  }
  return `Customer received HTTP ${result.status_code ?? "transport failure"} in ${result.latency_ms.toFixed(1)} ms`;
}

interface CaseInvestigationProps {
  item: DemoCase;
  live?: boolean;
  onApprove?: () => void;
  approving?: boolean;
}

export function CaseInvestigation({
  item,
  live = false,
  onApprove,
  approving = false,
}: CaseInvestigationProps) {
  const failureIndex = Math.max(
    0,
    servicePath.findIndex(([key]) => key === item.scenario.failure_stage),
  );
  const failedRequest = item.transaction_result?.succeeded === false;
  const degraded = item.transaction_result?.succeeded === true;
  const awaitingReview = item.review.status === "PENDING";
  const cutoff = transactionEndedAt(item);
  const requestEvents = item.evidence.filter(
    (event) => !isLateCaseEvent(item, event),
  );
  const lateEvents = item.evidence.filter((event) =>
    isLateCaseEvent(item, event),
  );

  return (
    <div
      className={`case-investigation ${live ? "case-investigation--live" : ""}`}
    >
      <section className="causal-banner">
        <span>
          <ServerCrash size={22} />
        </span>
        <div>
          <small>1 · Controlled cause</small>
          <strong>{item.scenario.cause_component}</strong>
          <p>{item.scenario.cause_action}</p>
        </div>
        <em>This is the deliberate test condition—not a model guess.</em>
      </section>

      <section className="architecture-flow">
        <header>
          <div>
            <small>2 · Observed customer request</small>
            <h3>Actual transaction architecture</h3>
          </div>
          <strong className={failedRequest ? "is-failed" : "is-complete"}>
            {outcomeText(item)}
          </strong>
        </header>
        <div
          className="architecture-flow__rail"
          role="list"
          aria-label="Ordered transaction stages"
        >
          {servicePath.map(([key, label, action], index) => {
            const events = requestEvents.filter(
              (event) => event.service === key,
            );
            let state = "blocked";
            let result = "Not reached after the affected stage";
            if (!item.transaction_result) {
              state = index === 0 ? "active" : "pending";
              result =
                index === 0 ? "Request is entering the gateway" : "Waiting";
            } else if (index < failureIndex) {
              state = index === 0 && failedRequest ? "propagated" : "complete";
              result =
                index === 0 && failedRequest
                  ? `Accepted request; later returned HTTP ${item.transaction_result.status_code}`
                  : events.length
                    ? `${events.length} request-correlated event${events.length === 1 ? "" : "s"}`
                    : "Completed before the failing stage";
            } else if (index === failureIndex) {
              state = degraded ? "degraded" : "failed";
              result = degraded
                ? "Request completed, but this stage carried the controlled degradation"
                : key === "authentication" &&
                    item.fault_id === "DATABASE_UNAVAILABLE"
                  ? "Blocked while PostgreSQL was unavailable"
                  : `First affected business stage: ${label}`;
            } else if (!failedRequest) {
              state = "complete";
              result = events.length
                ? "Observed in this request"
                : "Transaction continued";
            }
            return (
              <div
                className="architecture-flow__segment"
                key={key}
                role="listitem"
              >
                <article className={`architecture-stage is-${state}`}>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <strong>{label}</strong>
                    <small>{action}</small>
                    <em>{result}</em>
                  </div>
                </article>
                {index < servicePath.length - 1 && <ArrowRight size={22} />}
              </div>
            );
          })}
        </div>
        <div className="architecture-cause-link">
          <ArrowDown size={18} />
          <Database size={19} />
          <span>
            <strong>{item.scenario.cause_component}</strong>
            <small>Controlled dependency involved in this case</small>
          </span>
        </div>
        <div className="architecture-legend">
          <span>
            <i className="legend-complete" /> Completed before failure
          </span>
          <span>
            <i className="legend-propagated" /> Returned propagated error
          </span>
          <span>
            <i className="legend-failed" /> First affected stage
          </span>
          <span>
            <i className="legend-blocked" /> Not reached
          </span>
        </div>
      </section>

      <section className="request-story">
        <header>
          <small>3 · What happened, in time order</small>
          <h3>Evidence-backed request sequence</h3>
        </header>
        <ol>
          {requestEvents.map((event, index) => (
            <li
              className={eventFailed(event) ? "is-failed" : "is-complete"}
              key={`${event.timestamp}-${index}`}
            >
              <span>
                {eventFailed(event) ? (
                  <AlertTriangle size={17} />
                ) : (
                  <CheckCircle2 size={17} />
                )}
              </span>
              <time>{formatTime(event.timestamp)}</time>
              <div>
                <strong>{explainEvent(item, event)}</strong>
                <small>
                  {formatLabel(event.service)} · {formatLabel(event.event_type)}
                </small>
              </div>
            </li>
          ))}
          {!requestEvents.length && (
            <li className="is-pending">
              <span>
                <CircleDashed size={17} />
              </span>
              <div>
                <strong>Waiting for correlated service evidence</strong>
                <small>The request has not produced a stored event yet.</small>
              </div>
            </li>
          )}
        </ol>
        {lateEvents.length > 0 && (
          <details className="late-events">
            <summary>
              {lateEvents.length} late event{lateEvents.length === 1 ? "" : "s"}{" "}
              arrived after the customer response
            </summary>
            <p>
              These belong to the original request, but occurred after the
              gateway stopped waiting. They do not mean that the failed customer
              transaction recovered.
            </p>
            <ul>
              {lateEvents.map((event, index) => (
                <li key={`${event.timestamp}-${index}`}>
                  <time>{formatTime(event.timestamp)}</time>
                  <span>
                    <strong>{explainEvent(item, event)}</strong>
                    <small>
                      {formatLabel(event.service)} · original request ID
                    </small>
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {cutoff === null && item.transaction_result && (
          <p className="legacy-case-note">
            This older saved case does not contain an exact response cutoff. Run
            the scenario again to obtain late-event separation.
          </p>
        )}
      </section>

      <section
        className={`engineer-review ${awaitingReview ? "is-awaiting" : ""}`}
      >
        <header>
          <div>
            <small>4 · Human decision gate</small>
            <h3>Engineer review and remediation</h3>
          </div>
          <strong>
            {awaitingReview
              ? "Fault remains active"
              : formatLabel(item.review.status)}
          </strong>
        </header>
        {awaitingReview ? (
          <>
            <div className="engineer-review__body">
              <article>
                <FileSearch size={22} />
                <div>
                  <strong>Review the evidence</strong>
                  <p>
                    {item.evidence.length} exact request-correlated log events
                    are stored. The original customer result cannot be changed.
                  </p>
                </div>
              </article>
              <article>
                <AlertTriangle size={22} />
                <div>
                  <strong>Confirm the affected stage</strong>
                  <p>
                    {formatLabel(item.scenario.failure_stage)} is the first
                    business stage affected by the controlled condition.
                  </p>
                </div>
              </article>
              <article>
                <ShieldCheck size={22} />
                <div>
                  <strong>Proposed remediation</strong>
                  <p>{item.scenario.recovery_action}</p>
                </div>
              </article>
            </div>
            <div className="engineer-review__action">
              <span>
                <LockKeyhole size={20} />
                <span>
                  <strong>
                    No automatic recovery is presented as an engineering
                    decision.
                  </strong>
                  <small>
                    A safety watchdog removes the fault after{" "}
                    {item.review.timeout_seconds} seconds if the reviewer does
                    not respond.
                  </small>
                </span>
              </span>
              {onApprove && (
                <button
                  className="button button--primary button--large"
                  disabled={approving}
                  onClick={onApprove}
                >
                  {approving ? "Approving…" : "Approve remediation"}
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="engineer-review__decision">
            <CheckCircle2 size={22} />
            <div>
              <strong>
                {item.review.status === "APPROVED"
                  ? `Approved by ${item.review.approved_by}`
                  : item.review.status === "TIMED_OUT"
                    ? "Safety cleanup started after review timeout"
                    : "Evidence is still being prepared"}
              </strong>
              <p>
                {item.review.approved_at
                  ? `Decision recorded at ${formatTime(item.review.approved_at)}.`
                  : "The decision and cleanup lifecycle remain recorded in the case chronology."}
              </p>
            </div>
          </div>
        )}
      </section>

      <section
        className={`recovery-proof ${awaitingReview ? "is-locked" : ""}`}
      >
        <header>
          <small>5 · Separate recovery proof</small>
          <h3>
            {awaitingReview
              ? "Locked until engineer approval"
              : "Recovery used a different transaction"}
          </h3>
        </header>
        <div className="recovery-proof__grid">
          <article className={awaitingReview ? "is-pending" : "is-complete"}>
            <span>
              {awaitingReview ? (
                <LockKeyhole size={18} />
              ) : (
                <RotateCcw size={18} />
              )}
            </span>
            <div>
              <strong>
                {awaitingReview
                  ? "Cleanup not authorized"
                  : "Controlled cause removed"}
              </strong>
              <p>
                {awaitingReview
                  ? "The fault remains active while the case waits for review."
                  : item.scenario.recovery_action}
              </p>
            </div>
          </article>
          <article
            className={item.recovery_verified ? "is-complete" : "is-pending"}
          >
            <span>
              {item.recovery_verified ? (
                <CheckCircle2 size={18} />
              ) : (
                <CircleDashed size={18} />
              )}
            </span>
            <div>
              <strong>Infrastructure health</strong>
              <p>
                {item.recovery_verified
                  ? "Containers became healthy and the smoke check passed."
                  : "Health verification is still running."}
              </p>
            </div>
          </article>
          <article
            className={
              item.recovery_result?.succeeded ? "is-complete" : "is-pending"
            }
          >
            <span>
              {item.recovery_result?.succeeded ? (
                <CheckCircle2 size={18} />
              ) : (
                <CircleDashed size={18} />
              )}
            </span>
            <div>
              <strong>New recovery request</strong>
              <p className="mono">
                {item.recovery_result?.request_id ?? "Not issued yet"}
              </p>
            </div>
          </article>
          <article
            className={
              item.recovery_result?.succeeded ? "is-complete" : "is-pending"
            }
          >
            <span>
              {item.recovery_result?.succeeded ? (
                <CheckCircle2 size={18} />
              ) : (
                <CircleDashed size={18} />
              )}
            </span>
            <div>
              <strong>
                {item.recovery_result?.succeeded
                  ? `HTTP ${item.recovery_result.status_code} completed`
                  : "Recovery result pending"}
              </strong>
              <p>
                This result is separate from the failed request shown above.
              </p>
            </div>
          </article>
        </div>
      </section>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  Database,
  Play,
  Radio,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Topology } from "../components/Topology";
import { MetricCard, QueryState, StatusPill } from "../components/ui";
import { evaluation } from "../data/evaluation";
import { formatLabel, formatTime } from "../lib/format";

export function OverviewPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });
  const readiness = useQuery({
    queryKey: ["readiness"],
    queryFn: api.readiness,
    refetchInterval: 15_000,
  });
  const cases = useQuery({
    queryKey: ["demo-cases"],
    queryFn: api.demoCases,
    retry: false,
    refetchInterval: 10_000,
  });
  const latest = cases.data?.cases?.[0];

  return (
    <div className="overview-page">
      <section className="hero">
        <div className="hero__orb hero__orb--one" />
        <div className="hero__orb hero__orb--two" />
        <div className="hero__copy">
          <div className="hero__kicker">
            <Radio size={15} /> Explainable fault investigation
          </div>
          <h1>
            See the fault.
            <br />
            Trace the evidence.
          </h1>
          <p>
            Run a controlled transaction, follow its real service path, identify
            the first broken dependency and inspect the exact structured
            evidence behind the conclusion.
          </p>
          <div className="hero__actions">
            <Link className="button button--primary" to="/live">
              Run live investigation <Play size={17} />
            </Link>
            <Link className="button button--secondary" to="/cases">
              Review saved cases
            </Link>
          </div>
          <p className="hero__disclaimer">
            Controlled research environment · Probable origin, not guaranteed
            root cause
          </p>
        </div>
        <div className="hero__mockup">
          <div className="browser-bar">
            <span />
            <span />
            <span />
            <strong>faultweave / transaction-case</strong>
          </div>
          <div className="mockup__body">
            <div className="mockup__topline">
              <div>
                <small>Demonstration mode</small>
                <strong>One transaction · one evidence package</strong>
              </div>
              <StatusPill tone="success">Case ready</StatusPill>
            </div>
            <div className="overview-case-preview">
              <span>
                <ShieldCheck size={22} />
              </span>
              <div>
                <small>INVESTIGATION CONTRACT</small>
                <strong>
                  Execution order comes from request-correlated events
                </strong>
                <p>
                  Model output supports the diagnosis; it does not invent the
                  workflow.
                </p>
              </div>
            </div>
            <div className="mockup__topology">
              <Topology compact />
            </div>
          </div>
        </div>
      </section>

      <section className="content-section content-section--lifted">
        <div className="section-heading">
          <div>
            <p className="eyebrow eyebrow--blue">System readiness</p>
            <h2>Verified backend and demonstration evidence</h2>
          </div>
          <p>
            Live service health, frozen model readiness and persistent case
            history are shown separately.
          </p>
        </div>
        <div className="metrics-grid metrics-grid--four">
          <MetricCard
            eyebrow="Gateway"
            value={health.data?.status === "up" ? "Operational" : "Checking"}
            detail="Transaction microservices reachable"
            accent="green"
          />
          <MetricCard
            eyebrow="Frozen intelligence"
            value={readiness.data?.status === "ready" ? "Ready" : "Checking"}
            detail={`${readiness.data?.known_class_count ?? 9} known fault classes + open-set rejection`}
            accent="blue"
          />
          <MetricCard
            eyebrow="Saved cases"
            value={cases.data?.count ?? 0}
            detail={
              cases.isError
                ? "Start the Phase 15C case controller"
                : "Persistent SQLite evidence packages"
            }
            accent="violet"
          />
          <MetricCard
            eyebrow="Verified dataset"
            value={evaluation.dataset.runs}
            detail={`${evaluation.dataset.events.toLocaleString()} structured events`}
            accent="amber"
          />
        </div>
      </section>

      <section className="content-section two-column two-column--wide">
        <div className="panel workflow-panel">
          <div className="panel__header">
            <div>
              <h2>What happens in a live demonstration</h2>
              <p>
                Six presenter-controlled steps, each backed by a different
                evidence source.
              </p>
            </div>
          </div>
          <ol className="workflow-list">
            {[
              [
                "01",
                "Choose a scenario",
                "Select the business failure; safe technical parameters are fixed.",
              ],
              [
                "02",
                "Inject the controlled fault",
                "The affected component changes, but no customer traffic runs automatically.",
              ],
              [
                "03",
                "Send a real transaction",
                "The controller creates one request and trace identity only when you continue.",
              ],
              [
                "04",
                "Trace service execution",
                "Correlated events reconstruct authentication, account, transaction, payment and ledger order.",
              ],
              [
                "05",
                "Review the investigation",
                "The first failed event, downstream propagation and model assessment are shown separately.",
              ],
              [
                "06",
                "Approve and prove recovery",
                "An engineer approves cleanup before a separate normal transaction verifies recovery.",
              ],
            ].map(([number, title, text]) => (
              <li key={number}>
                <span>{number}</span>
                <div>
                  <strong>{title}</strong>
                  <p>{text}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
        <div className="panel latest-panel">
          <div className="panel__header">
            <div>
              <h2>Latest saved transaction case</h2>
              <p>
                Read from the persistent local case database—not gateway
                incident memory.
              </p>
            </div>
            <Database size={22} />
          </div>
          {cases.isLoading ? (
            <QueryState
              kind="loading"
              title="Loading case history"
              message="Reading the SQLite case store."
            />
          ) : cases.isError ? (
            <QueryState
              kind="error"
              title="Case controller unavailable"
              message="Restart the Phase 15C controller on port 18116."
            />
          ) : latest ? (
            <div className="latest-incident">
              <div className="latest-incident__heading">
                <StatusPill
                  tone={
                    latest.status === "RESOLVED"
                      ? "success"
                      : latest.status === "FAILED"
                        ? "danger"
                        : "warning"
                  }
                >
                  {formatLabel(latest.status)}
                </StatusPill>
                <small>{formatTime(latest.started_at)}</small>
              </div>
              <h3>{latest.scenario.title}</h3>
              <dl>
                <div>
                  <dt>First break</dt>
                  <dd>
                    {formatLabel(
                      latest.first_failure?.downstream_service ??
                        latest.first_failure?.service,
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Evidence</dt>
                  <dd>{latest.evidence.length} events</dd>
                </div>
                <div>
                  <dt>Recovery</dt>
                  <dd>{latest.recovery_verified ? "Verified" : "Pending"}</dd>
                </div>
              </dl>
              <Link className="text-link" to={`/cases/${latest.case_id}`}>
                Inspect saved case <ArrowRight size={15} />
              </Link>
            </div>
          ) : (
            <div className="healthy-empty">
              <CheckCircle2 size={36} />
              <h3>Ready for the first case</h3>
              <p>
                Run a scenario from Live Demo. Its complete evidence package
                will appear here.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

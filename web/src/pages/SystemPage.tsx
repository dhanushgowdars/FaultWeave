import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  CircleAlert,
  Cpu,
  Database,
  GitBranch,
  LockKeyhole,
  Monitor,
  RadioTower,
  ServerCog,
} from "lucide-react";
import { api } from "../api/client";
import { Topology } from "../components/Topology";
import { PageHeader, Panel, QueryState, StatusPill } from "../components/ui";
import { formatLabel } from "../lib/format";

export function SystemPage() {
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
  return (
    <div className="page-container">
      <PageHeader
        eyebrow="System"
        title="Runtime, models and service topology"
        description="Inspect what is running, what is frozen and which boundaries keep live inference separated from training."
      />
      <div className="system-status-grid">
        <div className="system-status-card">
          <span
            className={`system-status-card__icon ${health.data?.status === "up" ? "is-good" : ""}`}
          >
            <ServerCog />
          </span>
          <div>
            <small>API gateway</small>
            <strong>
              {health.isError
                ? "Unavailable"
                : health.data?.status === "up"
                  ? "Operational"
                  : "Checking"}
            </strong>
            <p>Transaction and intelligence boundary</p>
          </div>
          <StatusPill
            tone={health.data?.status === "up" ? "success" : "warning"}
          >
            {health.data?.status ?? "pending"}
          </StatusPill>
        </div>
        <div className="system-status-card">
          <span
            className={`system-status-card__icon ${readiness.data?.status === "ready" ? "is-good" : ""}`}
          >
            <Cpu />
          </span>
          <div>
            <small>Frozen intelligence</small>
            <strong>
              {readiness.isError
                ? "Not ready"
                : readiness.data?.status === "ready"
                  ? "Artifacts verified"
                  : "Checking"}
            </strong>
            <p>{readiness.data?.feature_count ?? 65} observable features</p>
          </div>
          <StatusPill
            tone={readiness.data?.status === "ready" ? "success" : "warning"}
          >
            {readiness.data?.status ?? "pending"}
          </StatusPill>
        </div>
      </div>
      {(health.isError || readiness.isError) && (
        <QueryState
          kind="error"
          title="Part of the runtime is unavailable"
          message="Start Docker services and mount the frozen model artifacts before the live demonstration."
        />
      )}
      <div className="two-column two-column--wide">
        <Panel
          title="Transaction dependency graph"
          subtitle="Numbered service responsibilities in actual execution order"
        >
          <Topology compact />
        </Panel>
        <Panel
          title="Frozen intelligence contract"
          subtitle="Runtime inference cannot silently change the research models"
        >
          <ul className="contract-list">
            <li>
              <LockKeyhole />
              <div>
                <strong>No runtime fitting</strong>
                <p>
                  Model training, feature selection and threshold calibration
                  are absent from the gateway.
                </p>
              </div>
            </li>
            <li>
              <GitBranch />
              <div>
                <strong>Two time scales</strong>
                <p>
                  10-second temporal detection and 60-second stable diagnosis
                  remain separate technical signals.
                </p>
              </div>
            </li>
            <li>
              <CircleAlert />
              <div>
                <strong>Open-set decision</strong>
                <p>
                  Unsupported known-class candidates become “Unknown abnormal
                  pattern.”
                </p>
              </div>
            </li>
            <li>
              <Database />
              <div>
                <strong>Observable evidence only</strong>
                <p>
                  Fault IDs, splits and expected origins never enter the
                  inference request.
                </p>
              </div>
            </li>
          </ul>
        </Panel>
      </div>
      <Panel
        title="Intelligence API surface"
        subtitle="Browser-facing read-only observation and evaluation boundaries"
      >
        <div className="api-grid">
          {[
            ["GET", "/api/v1/intelligence/ready", "Frozen artifact readiness"],
            [
              "GET",
              "/api/v1/intelligence/live/infer/{10|60}",
              "Current window inference",
            ],
            [
              "POST",
              "/api/v1/intelligence/live/evaluate",
              "Advance incident lifecycle",
            ],
            ["GET", "/api/v1/intelligence/stream", "Semantic SSE updates"],
            [
              "GET",
              "/api/v1/intelligence/incidents",
              "Bounded incident history",
            ],
            [
              "GET",
              "/api/v1/intelligence/live/telemetry/recent",
              "Recent structured evidence",
            ],
          ].map(([method, path, description]) => (
            <div className="api-row" key={path}>
              <StatusPill tone={method === "GET" ? "info" : "warning"}>
                {method}
              </StatusPill>
              <code>{path}</code>
              <span>{description}</span>
              <CheckCircle2 size={16} />
            </div>
          ))}
        </div>
      </Panel>
      <Panel
        title="Runtime components"
        subtitle="Processes required for the controlled case demonstration"
      >
        <div className="runtime-grid">
          {[
            [
              RadioTower,
              "Telemetry bridge",
              "Streams structured Docker events to the bounded gateway buffer.",
            ],
            [
              ServerCog,
              "Guided case controller",
              "Keeps injection, transaction, remediation and verification as separate approved actions.",
            ],
            [
              Monitor,
              "Dashboard",
              "Presents ordered execution, probable origin, exact evidence and separate recovery proof.",
            ],
          ].map(([Icon, title, text]) => {
            const Component = Icon as typeof RadioTower;
            return (
              <article key={String(title)}>
                <Component />
                <strong>{title as string}</strong>
                <p>{text as string}</p>
              </article>
            );
          })}
        </div>
        <p className="method-note">
          Current environment: {formatLabel(health.data?.service ?? "gateway")}{" "}
          · Phase 14 backend + Phase 15 dashboard.
        </p>
      </Panel>
    </div>
  );
}

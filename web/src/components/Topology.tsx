import {
  ArrowRight,
  Circle,
  Database,
  Landmark,
  LockKeyhole,
  Network,
  ReceiptText,
  WalletCards,
} from "lucide-react";
import type { TelemetryEvent } from "../api/types";

const services = [
  { key: "gateway", label: "API Gateway", action: "Accept request", icon: Network },
  { key: "authentication", label: "Authentication", action: "Verify login", icon: LockKeyhole },
  { key: "account", label: "Account", action: "Validate account", icon: Landmark },
  { key: "transaction", label: "Transaction", action: "Create transfer", icon: ReceiptText },
  { key: "payment", label: "Payment", action: "Process payment", icon: WalletCards },
  { key: "ledger", label: "Ledger", action: "Record entries", icon: Database },
] as const;

function eventFailed(event: TelemetryEvent) {
  return event.success === false || event.level === "ERROR" || Number(event.status_code) >= 400;
}

export function Topology({
  probableOrigin,
  affected = [],
  evidence,
  failureStage,
  compact = false,
}: {
  probableOrigin?: string | null;
  affected?: string[];
  evidence?: TelemetryEvent[];
  failureStage?: string | null;
  compact?: boolean;
}) {
  const origin = (probableOrigin ?? failureStage ?? "").toLowerCase();
  const firstFailureIndex = services.findIndex(({ key }) => origin.includes(key));
  const events = evidence ?? [];
  const caseMode = evidence !== undefined;

  return (
    <div className={`transaction-map ${compact ? "transaction-map--compact" : ""}`} aria-label="Directed transaction dependency path">
      <div className="transaction-map__caption">
        <strong>Directed transaction path</strong>
        <span>Numbered stages show actual request order. Red marks the first broken stage.</span>
      </div>
      <div className="transaction-map__path">
        {services.map(({ key, label, action, icon: Icon }, index) => {
          const serviceEvents = events.filter((event) => event.service === key);
          const failed = serviceEvents.some(eventFailed) || firstFailureIndex === index;
          const completed = serviceEvents.length > 0 && !failed;
          const blocked = firstFailureIndex >= 0 && index > firstFailureIndex && !serviceEvents.length;
          const impacted = affected.includes(key);
          const state = failed
            ? "failed"
            : blocked
              ? "blocked"
              : completed
                ? "complete"
                : impacted
                  ? "affected"
                  : caseMode
                    ? "pending"
                    : "reference";
          return (
            <div className="transaction-map__segment" key={key}>
              <article className={`transaction-node is-${state}`}>
                <span className="transaction-node__step">{String(index + 1).padStart(2, "0")}</span>
                <span className="transaction-node__icon"><Icon size={22} /></span>
                <div><strong>{label}</strong><small>{action}</small></div>
                <em>{failed ? "First break" : blocked ? "Not reached" : completed ? "Observed" : impacted ? "Impacted" : caseMode ? "Awaiting evidence" : "Service responsibility"}</em>
              </article>
              {index < services.length - 1 && <ArrowRight className={`transaction-arrow ${failed || blocked ? "is-broken" : ""}`} size={24} />}
            </div>
          );
        })}
      </div>
      <div className="transaction-map__note"><Circle size={10} /> Transaction writes final completion to Ledger after Payment succeeds; the exact write remains visible in case evidence.</div>
    </div>
  );
}

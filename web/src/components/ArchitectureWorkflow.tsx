import {
  BadgeCheck,
  CircleDollarSign,
  Database,
  FileCheck2,
  KeyRound,
  Landmark,
  Router,
  SendHorizontal,
} from "lucide-react";

import type { DemoSession, TransferEvidence } from "../api/types";
import { serviceLabel } from "../lib/presentation";
import { Badge } from "./ui";

type NodeState = "idle" | "active" | "success" | "danger" | "warning";

const nodes = [
  ["customer", "Customer", SendHorizontal, "Payment submitted"],
  ["gateway", "API Gateway", Router, "Request and trace assigned"],
  ["authentication", "Authentication", KeyRound, "Engineer access verified"],
  ["transaction", "Transaction", FileCheck2, "Durable workflow owner"],
  ["payment", "Payment", CircleDollarSign, "Coordinates account and ledger"],
  ["account", "Account", Landmark, "Moves money atomically"],
  ["ledger", "Ledger", BadgeCheck, "Saves permanent financial record"],
  ["database", "PostgreSQL", Database, "Authoritative persisted evidence"],
] as const;

type WorkflowNode = (typeof nodes)[number];

function eventService(value?: string) {
  const normalized = (value ?? "").toLowerCase();
  if (normalized.includes("auth")) return "authentication";
  if (normalized.includes("transaction")) return "transaction";
  if (normalized.includes("payment")) return "payment";
  if (normalized.includes("account")) return "account";
  if (normalized.includes("ledger")) return "ledger";
  if (normalized.includes("gateway")) return "gateway";
  return "database";
}

export function ArchitectureWorkflow({
  session,
  evidence,
  replayIndex,
  phase,
}: {
  session: DemoSession | null;
  evidence: TransferEvidence | null;
  replayIndex: number;
  phase: string;
}) {
  const visible = evidence?.timeline.slice(0, Math.max(replayIndex + 1, 0)) ?? [];
  const stateByNode = new Map<string, NodeState>();
  const detailByNode = new Map<string, string>();

  if (session) {
    stateByNode.set("customer", "success");
    stateByNode.set("gateway", phase === "executing" ? "active" : "success");
    stateByNode.set("authentication", "success");
    detailByNode.set("customer", `${session.presentation?.sender_name ?? "Sender"} → ${session.presentation?.recipient_name ?? "Recipient"}`);
    detailByNode.set("gateway", phase === "executing" ? "Waiting for persisted backend evidence" : `Session ${session.session_id.slice(0, 13)}…`);
    detailByNode.set("authentication", "Engineer role accepted by protected API");
  }
  visible.forEach((event, index) => {
    const key = eventService(event.service);
    const failed = ["FAILED", "ERROR"].includes(event.status.toUpperCase());
    stateByNode.set(key, failed ? "danger" : index === visible.length - 1 && replayIndex < (evidence?.timeline.length ?? 0) - 1 ? "active" : "success");
    detailByNode.set(key, `${event.step}: ${event.detail}`);
    stateByNode.set("database", failed ? "warning" : "success");
    detailByNode.set("database", "Timeline and case state persisted");
  });
  if (session?.state === "AWAITING_REVIEW") stateByNode.set("transaction", "warning");
  if (session?.state === "RECOVERED" || session?.state === "COMPLETED") stateByNode.set("database", "success");

  function renderNode([key, label, Icon, defaultDetail]: WorkflowNode, index: number) {
    const state = stateByNode.get(key) ?? "idle";
    return (
      <div className={`architecture-node node-${state}`} key={key} data-testid={`node-${key}`}>
        <div className="node-order">{index + 1}</div>
        <div className="node-icon"><Icon size={22} /></div>
        <div><strong>{label}</strong><small>{detailByNode.get(key) ?? defaultDetail}</small></div>
        <span className="node-state">{state === "idle" ? "waiting" : state}</span>
      </div>
    );
  }

  function renderRow(values: readonly WorkflowNode[], startIndex: number) {
    return values.map((node, offset) => (
      <div className="flow-segment" key={node[0]}>
        {renderNode(node, startIndex + offset)}
        {offset < values.length - 1 && <div className={`flow-arrow ${stateByNode.get(values[offset + 1][0]) ?? "idle"}`} aria-hidden="true">→</div>}
      </div>
    ));
  }

  return (
    <section className="workflow-card card">
      <div className="card-header">
        <div><span className="eyebrow">Evidence-backed architecture</span><h2>Live transaction workflow</h2><p>Only confirmed API state and persisted events change these services.</p></div>
        <Badge tone={phase === "executing" || phase === "replaying" ? "info" : session?.state === "AWAITING_REVIEW" ? "warning" : session ? "success" : "neutral"}>
          {phase === "executing" ? "BACKEND PROCESSING" : phase === "replaying" ? "VERIFIED EVENT REPLAY" : session?.state ?? "READY"}
        </Badge>
      </div>
      <div className="architecture-canvas">
        <div className="flow-caption"><span>Customer request enters the protected workflow</span><span>Follow stages 1 → 8</span></div>
        <div className="architecture-row">{renderRow(nodes.slice(0, 4), 0)}</div>
        <div className="architecture-turn"><span>Transaction delegates the financial operation</span><b>↓</b></div>
        <div className="architecture-row">{renderRow(nodes.slice(4, 8), 4)}</div>
      </div>
      <div className="workflow-legend">
        <span><i className="legend-dot active" />Active verified step</span>
        <span><i className="legend-dot success" />Persisted success</span>
        <span><i className="legend-dot danger" />First persisted failure</span>
        <span><i className="legend-dot warning" />Waiting for engineer</span>
      </div>
      {visible.length > 0 && replayIndex >= 0 && (
        <div className="current-event">
          <span>Now explaining persisted event {Math.min(replayIndex + 1, visible.length)} of {evidence?.timeline.length}</span>
          <strong>{serviceLabel(visible.at(-1)?.service)} · {visible.at(-1)?.step}</strong>
          <p>{visible.at(-1)?.detail}</p>
        </div>
      )}
    </section>
  );
}

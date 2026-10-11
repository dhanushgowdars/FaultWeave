import { useMemo, useState } from "react";
import { Database, Download, FileJson2, History, ListTree } from "lucide-react";

import type { DemoSession, TransferEvidence } from "../api/types";
import { Badge, EmptyState, SectionHeading } from "../components/ui";
import { EventTimeline } from "../components/EventTimeline";
import {
  channelLabel,
  formatDate,
  formatMoney,
  fundsLabel,
  latestEvidence,
  shortId,
  stateLabel,
  toneForStatus,
  unifyEvents,
} from "../lib/presentation";

type EvidenceTab = "story" | "logs" | "database" | "money";

export function EvidencePage({
  sessions,
  selected,
  evidence,
  onSelect,
  onExport,
  onOpenDemo,
}: {
  sessions: DemoSession[];
  selected: DemoSession | null;
  evidence: TransferEvidence | null;
  onSelect: (session: DemoSession) => void;
  onExport: () => void;
  onOpenDemo: () => void;
}) {
  const [tab, setTab] = useState<EvidenceTab>("story");
  const allEvents = useMemo(() => unifyEvents(selected?.events, evidence?.timeline), [selected, evidence]);
  if (!selected || !evidence) {
    return <EmptyState title="No case has been run in this demonstration" description="Investigation and evidence are intentionally empty. Complete a normal or fault workflow and it will be stored here as Case 1." action={<button className="button primary" onClick={onOpenDemo}>Run Case 1</button>} />;
  }
  const journalEntries = evidence.journal?.entries ?? [];
  return (
    <div className="page-stack">
      <SectionHeading eyebrow="Case evidence" title="Show exactly how FaultWeave reached its answer." description="Read the plain outcome first. Open structured events and stored financial proof only when a judge asks for technical evidence." />
      <div className="evidence-guide"><div className="primary"><span>Presentation order</span><strong>Outcome → ordered story → supporting proof</strong><p>Do not start with raw JSON. Explain the customer and money impact, then prove it.</p></div><div><span>Case state</span><strong>{stateLabel(selected.state)}</strong></div><div><span>Money</span><strong>{fundsLabel(evidence.funds_status)}</strong></div><div><span>Timeline events</span><strong>{evidence.timeline.length} persisted</strong></div></div>
      <section className="history-section card"><div className="card-header"><div><span className="eyebrow">This demonstration only</span><h2>Cases created after you pressed Run</h2><p>Older backend records are never mixed into this presentation.</p></div><Badge tone="info">{sessions.length} REAL {sessions.length === 1 ? "CASE" : "CASES"}</Badge></div><div className="history-list">{sessions.map((session, index) => { const itemEvidence = latestEvidence(session); return <button key={session.session_id} className={selected.session_id === session.session_id ? "history-item active" : "history-item"} onClick={() => onSelect(session)}><span className="case-number">{index + 1}</span><span><strong>Case {index + 1} · {session.scenario.title}</strong><small>{formatDate(session.updated_at)} · {formatMoney(session.transfer.amount_minor, session.transfer.currency)} · {session.presentation ? channelLabel(session.presentation.payment_channel) : "channel not recorded"}</small></span><Badge tone={session.state === "AWAITING_REVIEW" ? "warning" : "success"}>{stateLabel(session.state)}</Badge><em>{itemEvidence ? fundsLabel(itemEvidence.funds_status) : "EVIDENCE SNAPSHOT NOT READY"}</em></button>; })}</div></section>
      <section className="evidence-layout">
        <aside className="card evidence-summary"><Badge tone={selected.state === "AWAITING_REVIEW" ? "warning" : "success"}>{stateLabel(selected.state)}</Badge><h2>{formatMoney(selected.transfer.amount_minor, selected.transfer.currency)}</h2><p>{selected.presentation?.sender_name ?? selected.transfer.source_account_number} → {selected.presentation?.recipient_name ?? selected.transfer.destination_account_number}</p><div className="summary-kv"><span>Scenario</span><strong>{selected.scenario.title}</strong></div><div className="summary-kv"><span>Session ID</span><code title={selected.session_id}>{shortId(selected.session_id, 18)}</code></div><div className="summary-kv"><span>Transfer ID</span><code title={evidence.transfer_id}>{shortId(evidence.transfer_id, 18)}</code></div><div className="summary-kv"><span>Source account</span><code>{selected.transfer.source_account_number}</code></div><div className="summary-kv"><span>Destination</span><code>{selected.transfer.destination_account_number}</code></div><div className="summary-kv"><span>Payment channel</span><strong>{selected.presentation ? channelLabel(selected.presentation.payment_channel) : "Not recorded"}</strong></div><div className="summary-kv"><span>Duplicate key</span><code title={selected.transfer.idempotency_key}>{shortId(selected.transfer.idempotency_key, 18)}</code></div><button className="button secondary full" onClick={onExport}><Download size={16} />Download verified JSON</button></aside>
        <div className="card evidence-workbench"><div className="evidence-tabs">{([["story", ListTree, "Payment story"], ["logs", FileJson2, "Structured events"], ["database", Database, "Saved evidence"], ["money", History, "Money record"]] as const).map(([value, Icon, label]) => <button className={tab === value ? "active" : ""} onClick={() => setTab(value)} key={value}><Icon size={16} />{label}</button>)}</div>
          {tab === "story" && <div className="tab-content"><div className="tab-explainer"><strong>How to read this:</strong> exact timestamps determine order. A red event is a persisted failure; later warnings are consequences or review actions.</div><EventTimeline events={allEvents} /></div>}
          {tab === "logs" && <div className="tab-content"><div className="tab-explainer"><strong>What these are:</strong> normalized, machine-readable workflow events returned by the evidence API. They are not invented browser logs or raw Docker output.</div><div className="json-events">{evidence.timeline.map((event) => <article key={event.event_id}><div><Badge tone={toneForStatus(event.status) === "danger" ? "danger" : toneForStatus(event.status) === "warning" ? "warning" : "success"}>{event.status}</Badge><strong>{event.service}.{event.step}</strong></div><pre>{JSON.stringify(event, null, 2)}</pre><p>{event.detail}</p></article>)}</div></div>}
          {tab === "database" && <div className="tab-content"><div className="tab-explainer"><strong>What this proves:</strong> these are the evidence sources and durable case-history objects returned by the versioned backend contract.</div><div className="source-grid">{Object.entries(evidence.evidence_sources).map(([key, value]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}</div><h3>Persisted failure, review and recovery counts</h3><div className="count-grid"><div><strong>{evidence.case_history.failures.length}</strong><span>failure records</span></div><div><strong>{evidence.case_history.reviews.length}</strong><span>engineer reviews</span></div><div><strong>{evidence.case_history.recoveries.length}</strong><span>recoveries</span></div></div><details><summary>Inspect normalized case-history JSON</summary><pre>{JSON.stringify(evidence.case_history, null, 2)}</pre></details></div>}
          {tab === "money" && <div className="tab-content money-tab"><div className={`money-verdict ${evidence.funds_status === "COMMITTED" ? "committed" : "unknown"}`}><span>Authoritative money status</span><h2>{fundsLabel(evidence.funds_status)}</h2><p>{evidence.funds_status === "COMMITTED" ? journalEntries.length === 2 ? "The evidence contains one debit and one credit for the same transfer." : "Account movement is confirmed, but the permanent paired record may still require repair." : "FaultWeave does not claim that money moved. An engineer must reconcile account state and the duplicate-protection key."}</p></div>{journalEntries.length > 0 ? <div className="journal-table"><table><thead><tr><th>Direction</th><th>Account</th><th>Amount</th><th>Record ID</th></tr></thead><tbody>{journalEntries.map((entry, index) => <tr key={String(entry.id ?? index)}><td><Badge tone={entry.direction === "DEBIT" ? "warning" : "success"}>{String(entry.direction ?? "UNKNOWN")}</Badge></td><td><code>{String(entry.account_id ?? "not returned")}</code></td><td>{formatMoney(Number(entry.amount_minor ?? 0), String(entry.currency ?? "INR"))}</td><td><code>{shortId(String(entry.id ?? "—"), 16)}</code></td></tr>)}</tbody></table></div> : <div className="no-journal"><Database /><h3>No paired permanent record is returned yet</h3><p>This is not presented as success. The case remains incomplete or requires reconciliation.</p></div>}</div>}
        </div>
      </section>
      <section className="evidence-glossary"><div><strong>Request ID</strong><p>Identifies the customer’s one API request.</p></div><div><strong>Trace ID</strong><p>Connects that request across the participating services.</p></div><div><strong>Event ID</strong><p>Identifies one saved fact cited by the investigation and AI.</p></div><div><strong>Paired record</strong><p>One debit and one credit saved for the same transfer.</p></div></section>
    </div>
  );
}

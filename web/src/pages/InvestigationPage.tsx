import { Bot, CheckCircle2, CircleDollarSign, FileWarning, ShieldCheck, UserCheck } from "lucide-react";

import type { AssistantResponse, DemoSession, IntelligenceResult, TransferEvidence } from "../api/types";
import { fundsLabel, serviceLabel, stateLabel } from "../lib/presentation";
import { Badge, EmptyState, SectionHeading } from "../components/ui";

function Statement({ title, statement }: { title: string; statement?: { text: string; evidence_event_ids: string[]; kind: string } }) {
  if (!statement) return null;
  return <div className="statement-card"><span>{title}</span><strong>{statement.text}</strong><div className="statement-meta"><Badge tone={statement.kind === "observed_fact" ? "success" : statement.kind === "unconfirmed" ? "warning" : "info"}>{statement.kind.replaceAll("_", " ")}</Badge>{statement.evidence_event_ids.length > 0 ? statement.evidence_event_ids.map((id) => <a href={`#event-${id}`} key={id}>cites {id}</a>) : <em>No event citation claimed</em>}</div></div>;
}

export function InvestigationPage({
  session,
  evidence,
  assistant,
  intelligence,
  approvalNote,
  onApprovalNote,
  onApprove,
  onRecover,
  onOpenDemo,
  busy,
}: {
  session: DemoSession | null;
  evidence: TransferEvidence | null;
  assistant: AssistantResponse | null;
  intelligence: IntelligenceResult | null;
  approvalNote: string;
  onApprovalNote: (value: string) => void;
  onApprove: () => void;
  onRecover: () => void;
  onOpenDemo: () => void;
  busy: boolean;
}) {
  if (!session || !evidence) {
    return <EmptyState title="No case has been run in this demonstration" description="This page contains no sample or prefilled diagnosis. Finish a workflow and FaultWeave will create that case's evidence-grounded investigation." action={<button className="button primary" onClick={onOpenDemo}>Run Case 1</button>} />;
  }
  if (!assistant) {
    return <EmptyState title="Evidence exists; explanation is not ready" description="FaultWeave has not received a grounded assistant response for this case. No explanation is being invented." action={<button className="button secondary" onClick={onOpenDemo}>Return to workflow</button>} />;
  }
  const analysis = assistant.analysis;
  const hasFailure = evidence.case_history.failures.length > 0;
  const firstFailure = evidence.timeline.find((event) => ["FAILED", "ERROR"].includes(event.status.toUpperCase()))
    ?? evidence.case_history.failures.at(0);
  const recovered = session.state === "RECOVERED";
  const modelStatus = intelligence?.model?.status ?? "NOT RETURNED";
  const moneyExplanation = !hasFailure
    ? "The workflow completed and its stored financial proof confirms the transfer outcome."
    : evidence.funds_status === "COMMITTED"
      ? "The account operation is already confirmed. A later service failed, so FaultWeave paused completion instead of moving the money a second time."
      : "The stored evidence cannot prove whether the account operation finished. FaultWeave blocks an automatic retry until an engineer reconciles the original transfer.";
  return (
    <div className="page-stack">
      <SectionHeading eyebrow="Case investigation" title="Understand this payment incident in plain language." description="The answer below belongs to the currently selected real session. Evidence remains authoritative; AI translates it and cannot change money or recover services." />
      <section className="verdict-card card">
        <div className="verdict-top"><div><span className="eyebrow">Verified outcome</span><h2>{analysis.headline}</h2><p>{analysis.executive_summary}</p></div><Badge tone={recovered || session.state === "COMPLETED" ? "success" : "warning"}>{stateLabel(session.state)}</Badge></div>
        <div className="verdict-facts"><div><CircleDollarSign /><span>What happened to money<strong>{fundsLabel(evidence.funds_status)}</strong></span></div><div><FileWarning /><span>First affected service<strong>{hasFailure ? serviceLabel(String(firstFailure?.service ?? "Recorded below")) : "No service failed"}</strong></span></div><div><ShieldCheck /><span>Duplicate movement<strong>{session.verification?.single_funds_movement === true || evidence.funds_status === "COMMITTED" ? "Not repeated" : "No duplicate claimed"}</strong></span></div><div><UserCheck /><span>Human decision<strong>{session.state === "AWAITING_REVIEW" ? "Required" : session.state === "APPROVED" ? "Approved; not recovered yet" : "No pending decision"}</strong></span></div></div>
      </section>
      <section className="card why-card"><div><span className="eyebrow">Why this result is possible</span><h2>{hasFailure ? "The system separates money movement from later workflow confirmation." : "Every required step returned verified success."}</h2></div><p>{moneyExplanation}</p></section>
      <section className="investigation-grid">
        <div className="card investigation-main"><div className="card-header"><div><span className="eyebrow">What happened, in order</span><h2>Evidence-grounded explanation</h2></div><Bot /></div><Statement title="First thing that broke" statement={analysis.first_failure} /><Statement title="Likely cause" statement={analysis.probable_cause} /><Statement title="Money safety" statement={analysis.funds_state} /><Statement title="Recovery status" statement={analysis.recovery_assessment} /></div>
        <aside className="card engineer-panel"><span className="eyebrow">Engineer checklist</span><h2>What to verify next</h2><ol>{analysis.recommended_checks.map((check, index) => <li key={`${index}-${check}`}><span>{index + 1}</span>{check}</li>)}</ol><div className="authority-box"><strong>Decision authority</strong><p>Workflow and financial records determine the outcome. Model status: <b>{modelStatus}</b>.</p></div></aside>
      </section>
      <section className="card ai-panel"><div className="card-header"><div><span className="eyebrow">Grounded incident assistant</span><h2>AI translated this case into an engineer-ready answer</h2><p>Generated only after this workflow completed, using its stored evidence.</p></div><Badge tone={assistant.status === "AI_GENERATED" ? "success" : "warning"}>{assistant.status === "AI_GENERATED" ? `${assistant.provenance.provider.toUpperCase()} AI GENERATED` : "SAFE FALLBACK"}</Badge></div><div className="ai-answer">“{analysis.executive_summary}”</div><div className="ai-contribution-grid"><div><span>First failure</span><strong>{analysis.first_failure.text}</strong></div><div><span>Probable cause</span><strong>{analysis.probable_cause.text}</strong></div><div><span>Money outcome</span><strong>{analysis.funds_state.text}</strong></div><div><span>Recovery advice</span><strong>{analysis.recovery_assessment.text}</strong></div></div><div className="ai-provenance-grid"><div><span>Provider</span><strong>{assistant.provenance.provider}</strong></div><div><span>Model</span><strong>{assistant.provenance.model}</strong></div><div><span>Evidence hash</span><code>{assistant.provenance.evidence_sha256}</code></div><div><span>Ground truth leaked</span><strong>{assistant.provenance.ground_truth_used ? "YES" : "NO"}</strong></div></div><div className="ai-boundary"><CheckCircle2 /><p><strong>Read-only:</strong> AI explained cited evidence and suggested checks. It did not inject the fault, move money, approve recovery or call the recovery API.</p></div>{assistant.provenance.fallback_reason && <p className="fallback-note">Provider fallback reason: {assistant.provenance.fallback_reason}</p>}</section>
      {hasFailure && <section className="card approval-panel"><div><span className="eyebrow">Human recovery gate</span><h2>{session.state === "AWAITING_REVIEW" ? "Approve only after checking the evidence" : session.state === "APPROVED" ? "Approval saved. Recovery has not run." : "Recovery evidence is complete"}</h2><p>{session.state === "AWAITING_REVIEW" ? "Your note is persisted with this case. Approval does not itself move money or perform recovery." : session.state === "APPROVED" ? "The next action resumes this same idempotent transfer and then verifies balances and the paired record." : "The original failure, review, recovery and verification remain in the case history."}</p></div>{session.state === "AWAITING_REVIEW" && <div className="approval-actions"><textarea value={approvalNote} onChange={(event) => onApprovalNote(event.target.value)} placeholder="Record what you verified before approval (minimum 10 characters)" /><button className="button primary" disabled={busy || approvalNote.trim().length < 10} onClick={onApprove}>Approve same-transfer recovery</button></div>}{session.state === "APPROVED" && <button className="button danger-safe" disabled={busy} onClick={onRecover}>{busy ? "Recovery running…" : "Run approved recovery and verify"}</button>}{recovered && <Badge tone="success">ONE MONEY MOVEMENT + BALANCED RECORD VERIFIED</Badge>}</section>}
    </div>
  );
}

import { ArrowRight, BrainCircuit, Database, GitBranch, ShieldCheck } from "lucide-react";

import type { PageName } from "../api/types";
import { Badge } from "../components/ui";

export function OverviewPage({ onNavigate, caseCount }: { onNavigate: (page: PageName) => void; caseCount: number }) {
  return (
    <div className="page-stack">
      <section className="overview-hero">
        <div>
          <Badge tone="info">EVIDENCE-FIRST INCIDENT INTELLIGENCE</Badge>
          <h1>See a banking transfer fail.<br /><em>Understand exactly why.</em></h1>
          <p>FaultWeave executes a real durable demo transfer across six microservices, preserves the first failure, grounds the model and AI output in case evidence, then waits for an engineer before safe recovery.</p>
          <div className="hero-actions"><button className="button primary" onClick={() => onNavigate("demo")}>Open guided demonstration <ArrowRight size={17} /></button><button className="button secondary" disabled={caseCount === 0} onClick={() => onNavigate("evidence")}>{caseCount === 0 ? "Evidence appears after first run" : `Inspect ${caseCount} completed ${caseCount === 1 ? "case" : "cases"}`}</button></div>
        </div>
        <div className="hero-proof card">
          <div className="proof-label"><span className="live-dot" />Backend contract verified</div>
          <div className="proof-flow"><span>Transfer request</span><i>→</i><span>Persisted evidence</span><i>→</i><span>Human decision</span></div>
          <div className="proof-grid"><div><strong>6</strong><span>runnable cases</span></div><div><strong>7</strong><span>validated AI states</span></div><div><strong>0</strong><span>automatic recoveries</span></div></div>
          <p>The seventh AI state is the recovered Ledger case—not an invented seventh fault.</p>
        </div>
      </section>
      <section className="trust-strip"><div><ShieldCheck /><span><strong>Financial truth</strong>PostgreSQL-backed workflow and paired debit-credit record</span></div><div><GitBranch /><span><strong>Controlled failure</strong>Bounded fault owned by one session</span></div><div><BrainCircuit /><span><strong>Grounded AI</strong>Read-only explanation with event citations</span></div><div><Database /><span><strong>Durable proof</strong>Restart-persistent case and export hash</span></div></section>
      <section className="overview-grid">
        <div className="card feature-card"><span className="feature-number">01</span><h2>Run a genuine transfer</h2><p>Choose a normal payment or one of five controlled failures. The browser calls the Phase 17 orchestration API; it does not generate a pretend outcome.</p></div>
        <div className="card feature-card"><span className="feature-number">02</span><h2>Follow persisted events</h2><p>After the backend responds, exact event timestamps, services, results, request IDs and trace IDs replay in readable order.</p></div>
        <div className="card feature-card"><span className="feature-number">03</span><h2>Inspect and recover safely</h2><p>The assistant explains cited evidence. An engineer approves the same-transfer recovery. Verification proves that money moved only once.</p></div>
      </section>
      <section className="card system-map">
        <div className="card-header"><div><span className="eyebrow">Implemented architecture</span><h2>Six services, one evidence chain</h2></div><Badge tone="success">POSTGRESQL AUTHORITATIVE</Badge></div>
        <div className="system-map-row"><span>Customer</span><i>→</i><span>Gateway</span><i>→</i><span>Authentication</span><i>→</i><span>Transaction</span><i>→</i><span>Payment</span></div>
        <div className="system-map-branches"><div><b>Payment</b><i>→</i><span>Account</span><small>atomic sender debit + recipient credit</small></div><div><b>Payment</b><i>→</i><span>Ledger</span><small>permanent paired financial record</small></div><div><b>All business services</b><i>→</i><span>PostgreSQL</span><small>authoritative workflow evidence</small></div></div>
      </section>
    </div>
  );
}

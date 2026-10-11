import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, LoaderCircle, LockKeyhole, Server } from "lucide-react";

import { ApiError, FaultWeaveApi } from "./api/client";
import type {
  AssistantResponse,
  DemoScenario,
  DemoSession,
  IntelligenceResult,
  PageName,
  PaymentFormValue,
  TransferEvidence,
} from "./api/types";
import { ArchitectureWorkflow } from "./components/ArchitectureWorkflow";
import { EventTimeline } from "./components/EventTimeline";
import { PaymentBuilder } from "./components/PaymentBuilder";
import { Sidebar } from "./components/Sidebar";
import { Badge, SectionHeading } from "./components/ui";
import { fundsLabel, latestEvidence, stateLabel, unifyEvents } from "./lib/presentation";
import {
  ACTIVE_CASE_KEY,
  clearCurrentDemo,
  currentDemoSessions,
  readCurrentCaseIds,
  rememberCurrentCase,
} from "./lib/demoHistory";
import { EvaluationPage } from "./pages/EvaluationPage";
import { EvidencePage } from "./pages/EvidencePage";
import { IntelligencePage } from "./pages/IntelligencePage";
import { InvestigationPage } from "./pages/InvestigationPage";
import { OverviewPage } from "./pages/OverviewPage";

const TOKEN_KEY = "faultweave.engineer.token";
const REPLAY_MS = Number(import.meta.env.VITE_EVENT_REPLAY_MS ?? 2000);
const CASE_PAGES: PageName[] = ["investigation", "evidence", "intelligence"];

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function LoginPage({ onLogin, error, busy }: { onLogin: (username: string, password: string) => void; error: string | null; busy: boolean }) {
  const [username, setUsername] = useState("engineer");
  const [password, setPassword] = useState("faultweave-engineer");
  return <main className="login-page"><section className="login-story"><div className="brand large"><div className="brand-mark"><span /><span /><span /></div><div><strong>FaultWeave</strong><small>Evidence-first incident intelligence</small></div></div><div><Badge tone="info">JUDGE DEMONSTRATION</Badge><h1>Follow the money.<br /><em>Find the first failure.</em></h1><p>Run a genuine account-to-account transfer, preserve the exact evidence, obtain a grounded explanation and require a human decision before recovery.</p></div><div className="login-points"><div><CheckCircle2 /><span><strong>Six real backend cases</strong>One normal transfer and five controlled faults</span></div><div><CheckCircle2 /><span><strong>Seven investigation states</strong>Including post-recovery analysis</span></div><div><CheckCircle2 /><span><strong>No fake recovery</strong>Every mutation uses the protected API</span></div></div></section><section className="login-card card"><div className="login-icon"><LockKeyhole /></div><span className="eyebrow">Engineer access</span><h2>Open the command center</h2><p>Credentials are validated by the real Authentication service. Only the engineer role can control demonstrations.</p><form onSubmit={(event) => { event.preventDefault(); onLogin(username, password); }}><label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label><label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label>{error && <div className="error-banner"><AlertTriangle size={17} />{error}</div>}<button className="button primary full" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={17} />Authenticating…</> : <>Enter command center <ArrowRight size={17} /></>}</button></form><div className="local-note"><Server size={16} /><p>This is a controlled local research environment. It does not connect to a real bank or payment network.</p></div></section></main>;
}

function DemoPage({
  scenarios,
  session,
  evidence,
  replayIndex,
  phase,
  busy,
  onRun,
  onNavigate,
}: {
  scenarios: DemoScenario[];
  session: DemoSession | null;
  evidence: TransferEvidence | null;
  replayIndex: number;
  phase: string;
  busy: boolean;
  onRun: (value: PaymentFormValue) => void;
  onNavigate: (page: PageName) => void;
}) {
  const visibleEvidence = evidence ? { ...evidence, timeline: evidence.timeline.slice(0, Math.max(0, replayIndex + 1)) } : null;
  const events = unifyEvents(session?.events, visibleEvidence?.timeline);
  const failure = evidence?.timeline.find((event) => ["FAILED", "ERROR"].includes(event.status.toUpperCase()))
    ?? evidence?.case_history.failures.at(0);
  return <div className="page-stack"><SectionHeading eyebrow="Live banking demonstration" title="Enter one payment. Watch verified evidence unfold." description="The workflow calls the real Phase 17 backend. It waits for persisted results, then replays those exact events at a readable two-second pace." /><PaymentBuilder scenarios={scenarios} disabled={busy} onSubmit={onRun} /><ArchitectureWorkflow session={session} evidence={evidence} replayIndex={replayIndex} phase={phase} /><section className="demo-lower"><div className="card event-card"><div className="card-header"><div><span className="eyebrow">Exact event order</span><h2>Persisted transaction timeline</h2></div><Badge tone={events.length ? "success" : "neutral"}>{events.length} visible events</Badge></div><EventTimeline events={events} /></div><aside className="card live-result"><span className="eyebrow">Current result</span><h2>{session ? stateLabel(session.state) : "NOT STARTED"}</h2><div className="result-fact"><span>Money</span><strong>{fundsLabel(evidence?.funds_status)}</strong></div><div className="result-fact"><span>First failure</span><strong>{failure ? `${String(failure.service ?? "service")} · ${String(failure.step ?? "step")}` : evidence ? "None recorded" : "Waiting for evidence"}</strong></div><div className="result-fact"><span>AI analysis</span><strong>{session?.analysis_status === "READY" ? "Grounded explanation ready" : "Not requested or still running"}</strong></div><div className="result-fact"><span>Automatic recovery</span><strong>Disabled</strong></div>{session?.analysis_status === "READY" && <button className="button primary full" onClick={() => onNavigate("investigation")}>Open this investigation <ArrowRight size={16} /></button>}{evidence && <button className="button secondary full" onClick={() => onNavigate("evidence")}>Inspect case evidence</button>}<p className="truth-note">A green diagram node means a returned event confirmed success. A red node means the evidence API persisted a failure.</p></aside></section></div>;
}

export default function App() {
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem(TOKEN_KEY));
  const api = useMemo(() => new FaultWeaveApi(token), [token]);
  const [page, setPage] = useState<PageName>("overview");
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [sessions, setSessions] = useState<DemoSession[]>([]);
  const [, setCaseIds] = useState<string[]>(() => readCurrentCaseIds());
  const [session, setSession] = useState<DemoSession | null>(null);
  const [evidence, setEvidence] = useState<TransferEvidence | null>(null);
  const [assistant, setAssistant] = useState<AssistantResponse | null>(null);
  const [intelligence, setIntelligence] = useState<IntelligenceResult | null>(null);
  const [phase, setPhase] = useState("idle");
  const [replayIndex, setReplayIndex] = useState(-1);
  const [busy, setBusy] = useState(false);
  const [backendReady, setBackendReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [approvalNote, setApprovalNote] = useState("Reviewed persisted failure, money status and duplicate-protection evidence; approve safe same-transfer recovery.");

  const handleFailure = useCallback((caught: unknown) => {
    const message = caught instanceof Error ? caught.message : "Unexpected command-center error";
    setError(message);
    setNotice(null);
    setBusy(false);
    setPhase("idle");
    if (caught instanceof ApiError && caught.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken(null);
    }
  }, []);

  const refreshHistory = useCallback(async (visibleCaseIds = readCurrentCaseIds()) => {
    const values = await api.sessions();
    const visible = currentDemoSessions(values, visibleCaseIds);
    setSessions(visible);
    return visible;
  }, [api]);

  const selectSession = useCallback(async (selected: DemoSession) => {
    setBusy(true);
    setError(null);
    try {
      const restored = await api.getSession(selected.session_id);
      let restoredEvidence = latestEvidence(restored);
      if (restored.transfer_id) restoredEvidence = await api.evidence(restored.transfer_id);
      setSession(restored);
      setEvidence(restoredEvidence);
      setAssistant((restored.snapshots.assistant as AssistantResponse | undefined) ?? null);
      setIntelligence((restored.snapshots.intelligence as IntelligenceResult | undefined) ?? null);
      setReplayIndex((restoredEvidence?.timeline.length ?? 0) - 1);
      sessionStorage.setItem(ACTIVE_CASE_KEY, restored.session_id);
      const position = readCurrentCaseIds().indexOf(restored.session_id) + 1;
      setNotice(`Opened Case ${position > 0 ? position : "—"}: ${restored.scenario.title}.`);
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setBusy(false);
    }
  }, [api, handleFailure]);

  useEffect(() => {
    if (!token) return;
    let active = true;
    (async () => {
      try {
        const [health, scenarioValues, history] = await Promise.all([api.health(), api.scenarios(), api.sessions()]);
        if (!active) return;
        setBackendReady(health.status === "up");
        setScenarios(scenarioValues);
        const visibleIds = readCurrentCaseIds();
        setCaseIds(visibleIds);
        const visibleHistory = currentDemoSessions(history, visibleIds);
        setSessions(visibleHistory);
        const remembered = sessionStorage.getItem(ACTIVE_CASE_KEY);
        const selected = visibleHistory.find((item) => item.session_id === remembered);
        if (selected) await selectSession(selected);
        if (!selected) {
          setSession(null);
          setEvidence(null);
          setAssistant(null);
          setIntelligence(null);
          setReplayIndex(-1);
        }
      } catch (caught) {
        if (active) handleFailure(caught);
      }
    })();
    return () => { active = false; };
  }, [api, handleFailure, selectSession, token]);

  async function login(username: string, password: string) {
    setBusy(true);
    setError(null);
    try {
      const response = await api.login(username, password);
      if (response.role !== "engineer") throw new Error("The command center requires the engineer role.");
      sessionStorage.setItem(TOKEN_KEY, response.access_token);
      setToken(response.access_token);
      setNotice("Engineer authentication verified. This demonstration starts with no selected case.");
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  async function replay(timelineLength: number, startAt = 0) {
    setPhase("replaying");
    for (let index = startAt; index < timelineLength; index += 1) {
      setReplayIndex(index);
      await wait(REPLAY_MS);
    }
  }

  async function runWorkflow(value: PaymentFormValue) {
    setBusy(true);
    setError(null);
    setNotice(null);
    setSession(null);
    setEvidence(null);
    setAssistant(null);
    setIntelligence(null);
    setReplayIndex(-1);
    setPage("demo");
    try {
      setPhase("preparing");
      let current = await api.createSession(value);
      setSession(current);
      if (current.scenario.fault_id) {
        setPhase("arming");
        current = await api.arm(current.session_id);
        setSession(current);
      }
      setPhase("executing");
      const executed = await api.execute(current.session_id);
      setSession(executed.session);
      setEvidence(executed.evidence);
      await replay(executed.evidence.timeline.length);
      setPhase("analyzing");
      const analyzed = await api.analyze(current.session_id);
      setSession(analyzed.session);
      setEvidence(analyzed.evidence);
      setAssistant(analyzed.assistant);
      setIntelligence(analyzed.intelligence);
      setReplayIndex(analyzed.evidence.timeline.length - 1);
      setPhase("complete");
      const visibleIds = rememberCurrentCase(analyzed.session.session_id);
      setCaseIds(visibleIds);
      await refreshHistory(visibleIds);
      const caseNumber = visibleIds.indexOf(analyzed.session.session_id) + 1;
      setNotice(analyzed.session.state === "AWAITING_REVIEW" ? `Case ${caseNumber} saved: the failure is preserved and waiting for engineer review.` : `Case ${caseNumber} saved: the transfer and its financial evidence completed successfully.`);
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const approved = await api.approve(session.session_id, approvalNote);
      setSession(approved);
      await refreshHistory();
      setNotice("Engineer approval persisted. Recovery has not run yet.");
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  async function recover() {
    if (!session || !evidence) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setPage("demo");
      setPhase("recovering");
      const previousCount = evidence.timeline.length;
      const recovered = await api.recover(session.session_id);
      setSession(recovered.session);
      setEvidence(recovered.evidence);
      await replay(recovered.evidence.timeline.length, previousCount);
      setPhase("analyzing");
      const analyzed = await api.analyze(session.session_id);
      setSession(analyzed.session);
      setEvidence(analyzed.evidence);
      setAssistant(analyzed.assistant);
      setIntelligence(analyzed.intelligence);
      setReplayIndex(analyzed.evidence.timeline.length - 1);
      setPhase("complete");
      await refreshHistory();
      setNotice("Recovery verified: the same transfer completed with one funds movement and a balanced record.");
      setPage("investigation");
    } catch (caught) {
      handleFailure(caught);
    } finally {
      setBusy(false);
    }
  }

  async function exportEvidence() {
    if (!session?.transfer_id) return;
    try {
      const payload = await api.exportEvidence(session.transfer_id);
      const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `faultweave-evidence-${session.transfer_id}.json`;
      link.click();
      URL.revokeObjectURL(url);
      setNotice("The backend evidence export was downloaded with its integrity hash.");
    } catch (caught) {
      handleFailure(caught);
    }
  }

  function navigate(target: PageName) {
    if (CASE_PAGES.includes(target) && sessions.length === 0) {
      setNotice("Run the first workflow before opening Investigation, Evidence or Intelligence.");
      setPage("demo");
      return;
    }
    setPage(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function logout() {
    sessionStorage.removeItem(TOKEN_KEY);
    clearCurrentDemo();
    setCaseIds([]);
    setSessions([]);
    setSession(null);
    setEvidence(null);
    setAssistant(null);
    setIntelligence(null);
    setToken(null);
  }

  if (!token) return <LoginPage onLogin={login} error={error} busy={busy} />;

  return <div className="app-shell"><Sidebar page={page} onNavigate={navigate} onLogout={logout} caseCount={sessions.length} /><main className="app-main"><header className="topbar"><div><span className="topbar-kicker">FaultWeave / Phase 18</span><strong>{page === "demo" ? "Live demonstration" : page === "investigation" ? "Case investigation" : page === "evidence" ? "Case evidence" : page === "intelligence" ? "Layered intelligence" : page === "evaluation" ? "Research evaluation" : "System overview"}</strong></div><div className="topbar-status"><span className={backendReady ? "backend-dot ready" : "backend-dot"} />{backendReady ? "Backend ready" : "Backend unavailable"}<Badge tone={sessions.length > 0 ? "info" : "neutral"}>{sessions.length > 0 ? `${sessions.length} ${sessions.length === 1 ? "CASE" : "CASES"} THIS DEMO` : "NO CASE RUN YET"}</Badge>{session && <Badge tone={session.state === "AWAITING_REVIEW" ? "warning" : "success"}>{stateLabel(session.state)}</Badge>}</div></header>{error && <div className="global-message error"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => setError(null)}>Dismiss</button></div>}{notice && <div className="global-message success"><CheckCircle2 size={18} /><span>{notice}</span><button onClick={() => setNotice(null)}>Dismiss</button></div>}<div className="page-container">{page === "overview" && <OverviewPage onNavigate={navigate} caseCount={sessions.length} />}{page === "demo" && <DemoPage scenarios={scenarios} session={session} evidence={evidence} replayIndex={replayIndex} phase={phase} busy={busy} onRun={runWorkflow} onNavigate={navigate} />}{page === "investigation" && <InvestigationPage session={session} evidence={evidence} assistant={assistant} intelligence={intelligence} approvalNote={approvalNote} onApprovalNote={setApprovalNote} onApprove={approve} onRecover={recover} onOpenDemo={() => navigate("demo")} busy={busy} />}{page === "evidence" && <EvidencePage sessions={sessions} selected={session} evidence={evidence} onSelect={selectSession} onExport={exportEvidence} onOpenDemo={() => navigate("demo")} />}{page === "intelligence" && <IntelligencePage intelligence={intelligence} assistant={assistant} evidence={evidence} />}{page === "evaluation" && <EvaluationPage />}</div></main></div>;
}

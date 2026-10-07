import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Play,
  ShieldCheck,
} from "lucide-react";
import { api } from "../api/client";
import type { DemoFault } from "../api/types";
import { GuidedDemoWorkspace } from "../components/GuidedDemoWorkspace";
import { PageHeader, QueryState, StatusPill } from "../components/ui";
import { formatLabel } from "../lib/format";

type GuidedAction = "transaction" | "remediate" | "verify";

export function LiveDemoPage() {
  const queryClient = useQueryClient();
  const [faultId, setFaultId] = useState("SERVICE_UNAVAILABLE");
  const [reviewer, setReviewer] = useState("");
  const catalog = useQuery({
    queryKey: ["demo-catalog"],
    queryFn: api.demoCatalog,
    retry: false,
  });
  const demo = useQuery({
    queryKey: ["demo-status"],
    queryFn: api.demoStatus,
    retry: false,
    refetchInterval: 1_000,
  });
  const controllerCompatible =
    catalog.data?.api_version === "5.0" &&
    demo.isSuccess &&
    Object.hasOwn(demo.data ?? {}, "case");
  const faults = useMemo(
    () => (controllerCompatible ? (catalog.data?.faults ?? []) : []),
    [catalog.data?.faults, controllerCompatible],
  );
  const primary = faults.filter((fault) => fault.primary_demo);
  const secondary = faults.filter((fault) => !fault.primary_demo);
  const selected = faults.find((fault) => fault.fault_id === faultId);
  const currentCase = demo.data?.case;

  useEffect(() => {
    if (faults.length && !faults.some((fault) => fault.fault_id === faultId)) {
      setFaultId(faults[0].fault_id);
    }
  }, [faultId, faults]);

  const refresh = () => queryClient.invalidateQueries();
  const inject = useMutation({ mutationFn: api.startDemo, onSuccess: refresh });
  const transact = useMutation({
    mutationFn: api.runDemoTransaction,
    onSuccess: refresh,
  });
  const remediate = useMutation({
    mutationFn: (caseId: string) =>
      api.approveRemediation(caseId, reviewer.trim()),
    onSuccess: refresh,
  });
  const verify = useMutation({
    mutationFn: api.verifyDemoRecovery,
    onSuccess: refresh,
  });
  const busy =
    inject.isPending ||
    transact.isPending ||
    remediate.isPending ||
    verify.isPending;
  const error =
    inject.error ?? transact.error ?? remediate.error ?? verify.error;

  function handleAction(action: GuidedAction) {
    if (!currentCase) return;
    if (action === "transaction") transact.mutate(currentCase.case_id);
    if (action === "remediate" && reviewer.trim().length >= 2)
      remediate.mutate(currentCase.case_id);
    if (action === "verify") verify.mutate(currentCase.case_id);
  }

  return (
    <div className="page-container page-container--wide demo-lab-page">
      <PageHeader
        eyebrow="Guided incident demonstration"
        title="From transaction failure to verified recovery"
        description="You control every step: inject one fault, send the disclosed test traffic, inspect the highlighted request, approve remediation and verify with a new request."
      />

      <section className="demo-purpose">
        <ShieldCheck size={26} />
        <div>
          <strong>What FaultWeave demonstrates</strong>
          <p>
            It observes a distributed transaction, detects abnormal behaviour,
            explains the evidence, identifies the probable failure origin and
            preserves an auditable recovery record.
          </p>
        </div>
        <span>Real services · Frozen models · Correlated logs</span>
      </section>

      {!controllerCompatible && (
        <div className="demo-notice demo-notice--warning">
          <AlertTriangle size={24} />
          <div>
            <strong>Guided controller unavailable</strong>
            <p>
              Restart <code>python scripts/live_demo_control.py</code>. This
              page requires API contract 5.0 and will not imitate a case using
              browser timers.
            </p>
          </div>
          <StatusPill tone="warning">Restart required</StatusPill>
        </div>
      )}

      {!currentCase || !demo.data?.active ? (
        <section className="scenario-launcher">
          <header>
            <span>01</span>
            <div>
              <small>Choose what should fail</small>
              <h2>Select a controlled scenario</h2>
              <p>
                Payment Service Unavailable is recommended because its customer
                impact and dependency evidence are easiest to explain to judges.
              </p>
            </div>
          </header>
          <div className="scenario-launcher__grid">
            {primary.map((fault) => (
              <ScenarioOption
                key={fault.fault_id}
                fault={fault}
                selected={fault.fault_id === faultId}
                onSelect={setFaultId}
              />
            ))}
          </div>
          <details className="advanced-scenarios">
            <summary>
              <ChevronDown size={16} /> Four advanced engineering scenarios
            </summary>
            <div className="scenario-launcher__grid">
              {secondary.map((fault) => (
                <ScenarioOption
                  key={fault.fault_id}
                  fault={fault}
                  selected={fault.fault_id === faultId}
                  onSelect={setFaultId}
                />
              ))}
            </div>
          </details>
          {selected && (
            <div className="selected-scenario">
              <div>
                <small>Selected demonstration</small>
                <strong>{selected.title}</strong>
                <p>{selected.customer_impact}</p>
              </div>
              <div>
                <small>First affected stage</small>
                <strong>{formatLabel(selected.failure_stage)}</strong>
                <p>{selected.cause_action}</p>
              </div>
              <button
                className="button button--danger button--large"
                disabled={!controllerCompatible || busy}
                onClick={() => inject.mutate({ fault_id: selected.fault_id })}
              >
                <Play size={18} />
                {busy ? "Injecting…" : "Inject controlled fault"}
              </button>
            </div>
          )}
        </section>
      ) : (
        <section className="scenario-locked">
          <CheckCircle2 size={20} />
          <div>
            <strong>Scenario locked for this case</strong>
            <p>
              {currentCase.scenario.title}. Finish the guided workflow before
              starting another case.
            </p>
          </div>
        </section>
      )}

      {error && <div className="inline-error">{error.message}</div>}

      {currentCase?.status === "AWAITING_REVIEW" && (
        <div className="guided-reviewer">
          <label htmlFor="demo-reviewer">Engineer approving remediation</label>
          <input
            id="demo-reviewer"
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            placeholder="Enter your name before approval"
            maxLength={80}
          />
          {reviewer.trim().length < 2 && <small>Enter at least two characters to approve.</small>}
        </div>
      )}

      {currentCase ? (
        <GuidedDemoWorkspace
          item={currentCase}
          busy={busy}
          canApprove={reviewer.trim().length >= 2}
          onAction={handleAction}
        />
      ) : (
        <QueryState
          kind="empty"
          title="Ready for a guided case"
          message="Choose a scenario and inject the controlled fault. No transaction will run until you explicitly request it."
        />
      )}
    </div>
  );
}

function ScenarioOption({
  fault,
  selected,
  onSelect,
}: {
  fault: DemoFault;
  selected: boolean;
  onSelect: (faultId: string) => void;
}) {
  return (
    <button
      className={`scenario-option ${selected ? "is-selected" : ""}`}
      onClick={() => onSelect(fault.fault_id)}
    >
      <span>{selected ? <CheckCircle2 size={18} /> : null}</span>
      <div>
        <strong>{fault.title}</strong>
        <p>{fault.customer_impact}</p>
        <small>{formatLabel(fault.failure_stage)} stage</small>
      </div>
    </button>
  );
}

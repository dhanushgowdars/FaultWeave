export type PageName =
  | "overview"
  | "demo"
  | "investigation"
  | "evidence"
  | "intelligence"
  | "evaluation";

export type PaymentChannel = "UPI" | "DEBIT_CARD" | "NET_BANKING";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  role: string;
}

export interface DemoScenario {
  scenario_id: string;
  title: string;
  summary: string;
  fault_id: string | null;
  target: string | null;
  intensity: string | null;
  expected_step: string | null;
  expected_funds_status: string;
  recommended: boolean;
}

export interface DemoTransferInput {
  source_account_number: string;
  destination_account_number: string;
  amount_minor: number;
  currency: string;
  idempotency_key: string;
}

export interface DemoPresentation {
  sender_name: string;
  recipient_name: string;
  payment_channel: PaymentChannel;
  purpose: string;
}

export interface DemoEvent {
  sequence: number;
  timestamp: string;
  action: string;
  status: string;
  detail: string;
}

export interface DemoSession {
  contract_version: string;
  session_id: string;
  state: string;
  scenario: DemoScenario;
  transfer: DemoTransferInput;
  presentation?: DemoPresentation;
  transfer_id: string | null;
  created_at: string;
  updated_at: string;
  analysis_status: string;
  approval: Record<string, unknown> | null;
  verification: {
    status: string;
    same_transfer_id: string;
    single_funds_movement: boolean;
    balanced_journal: boolean;
    source_balance_minor: number;
    destination_balance_minor: number;
  } | null;
  snapshots: {
    evidence_after_execution?: TransferEvidence;
    evidence_after_recovery?: TransferEvidence;
    latest_evidence?: TransferEvidence;
    intelligence?: IntelligenceResult;
    assistant?: AssistantResponse;
    balances_before?: Record<string, unknown>;
    balances_after?: Record<string, unknown>;
  };
  events: DemoEvent[];
}

export interface TransferTimelineEvent {
  event_id: string;
  sequence: number;
  attempt: number;
  timestamp: string;
  service: string;
  step: string;
  status: string;
  detail: string;
  request_id: string | null;
  trace_id: string | null;
  run_id: string | null;
}

export interface JournalEntry {
  id?: string;
  transfer_id?: string;
  account_id?: string;
  direction?: string;
  amount_minor?: number;
  currency?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface TransferEvidence {
  schema_version: string;
  transfer_id: string;
  state: string;
  created_at: string;
  transfer: DemoTransferInput;
  funds_status: string;
  requires_review: boolean;
  timeline: TransferTimelineEvent[];
  case_history: {
    failures: Array<Record<string, unknown>>;
    reviews: Array<Record<string, unknown>>;
    recoveries: Array<Record<string, unknown>>;
  };
  investigation: Record<string, unknown>;
  journal: { entries?: JournalEntry[]; [key: string]: unknown } | null;
  evidence_sources: Record<string, string>;
}

export interface AssistantStatement {
  kind: "observed_fact" | "model_prediction" | "ai_inference" | "unconfirmed";
  text: string;
  evidence_event_ids: string[];
}

export interface AssistantResponse {
  schema_version: string;
  transfer_id: string;
  focus: string;
  status: "AI_GENERATED" | "DETERMINISTIC_FALLBACK";
  analysis: {
    headline: string;
    executive_summary: string;
    first_failure: AssistantStatement;
    funds_state: AssistantStatement;
    model_assessment: AssistantStatement;
    probable_cause: AssistantStatement;
    observed_facts: AssistantStatement[];
    recommended_checks: string[];
    recovery_assessment: AssistantStatement;
    limitations: string[];
  };
  evidence_index: Record<string, Record<string, unknown>>;
  provenance: {
    provider: string;
    model: string;
    mode: string;
    evidence_sha256: string;
    generated_at: string;
    cached: boolean;
    fallback_reason: string | null;
    ground_truth_used: false;
  };
  safety: Record<string, unknown>;
}

export interface IntelligenceResult {
  schema_version?: string;
  transfer_id?: string;
  transfer_state?: string;
  decision_authority?: Record<string, unknown>;
  operational_facts?: Record<string, unknown>;
  probable_origin?: Record<string, unknown> | null;
  explanation?: Record<string, string>;
  model?: {
    status?: string;
    reason?: string | null;
    scope?: string;
    result?: Record<string, unknown> | null;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface AnalysisResponse {
  session: DemoSession;
  evidence: TransferEvidence;
  intelligence: IntelligenceResult;
  assistant: AssistantResponse;
}

export interface PaymentFormValue {
  scenario_id: string;
  source_account_number: string;
  destination_account_number: string;
  amount_minor: number;
  currency: "INR";
  sender_name: string;
  recipient_name: string;
  payment_channel: PaymentChannel;
  purpose: string;
}

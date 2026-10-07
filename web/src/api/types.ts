export type ConnectionState =
  "connecting" | "connected" | "reconnecting" | "offline";

export interface Readiness {
  status: string;
  main_window_seconds?: number;
  temporal_window_seconds?: number;
  feature_count?: number;
  known_class_count?: number;
  open_set_label?: string;
  [key: string]: unknown;
}

export interface Classification {
  label: string;
  known_class_candidate: string | null;
  confidence: number | null;
  class_support_score: number | null;
  class_support_limit: number | null;
  accepted_as_known: boolean | null;
}

export interface AnomalyDecision {
  score: number;
  threshold: number;
  is_anomalous: boolean;
}

export interface LocalizationCandidate {
  candidate: string;
  kind: string;
  score: number;
  first_failure_seconds: number | null;
  reasons: string[];
  evidence: Record<string, number>;
}

export interface Incident {
  incident_id: string;
  state: "ACTIVE" | "RESOLVED";
  detected_at: string;
  updated_at: string;
  resolved_at: string | null;
  detection_source: string;
  signal_started_at: string;
  active_duration_seconds?: number;
  latest_main_status: string | null;
  classification: Classification | null;
  localization: {
    method: string;
    probable_origin: string | null;
    top_3: LocalizationCandidate[];
  } | null;
  severity: {
    score: number;
    level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    components: Record<string, number>;
    affected_services: string[];
  } | null;
  baseline: {
    available: boolean;
    window_seconds: number;
    window_started_at: string | null;
    window_ended_at: string | null;
  };
  temporal: {
    latest_status: string | null;
    latest_anomaly: AnomalyDecision | null;
    samples_last_60s: number;
    anomalous_samples_last_60s: number;
    consecutive_normal_windows: number;
  };
}

export interface IncidentList {
  count: number;
  incidents: Incident[];
}

export interface CurrentIncident {
  active: boolean;
  incident: Incident | null;
}

export interface LiveInference {
  window: {
    window_seconds: number;
    window_started_at: string;
    window_ended_at: string;
    source_events: number;
    source_requests: number;
    request_measurement_source: string;
    features: Record<string, number>;
  };
  result: {
    window_seconds: number;
    pipeline: string;
    status: string;
    anomaly: AnomalyDecision;
    classification: Classification | null;
  };
}

export interface TelemetryEvent {
  timestamp: string;
  service: string;
  level: string;
  event_type: string;
  message: string;
  request_id?: string | null;
  trace_id?: string | null;
  status_code?: number | null;
  latency_ms?: number | null;
  downstream_service?: string | null;
  error_type?: string | null;
  [key: string]: unknown;
}

export interface TelemetryEvidence {
  count: number;
  buffered_events: number;
  buffered_requests: number;
  watermark: string | null;
  events: TelemetryEvent[];
}

export interface StreamEvent {
  id: string;
  event: string;
  receivedAt: string;
  data: Record<string, unknown>;
}

export interface DemoFault {
  fault_id: string;
  tier: "core" | "extended";
  mechanism: string;
  description: string;
  title: string;
  customer_impact: string;
  target: string;
  failure_stage: string;
  cause_component: string;
  cause_action: string;
  recovery_action: string;
  primary_demo: boolean;
  request_count: number;
}

export interface DemoCatalog {
  api_version: string;
  faults: DemoFault[];
  journey: string[];
  case_contract: string;
}

export interface DemoStatus {
  status:
    | "IDLE"
    | "QUEUED"
    | "INJECTING"
    | "FAULT_ACTIVE"
    | "TRANSACTION_RUNNING"
    | "AWAITING_REVIEW"
    | "REMEDIATING"
    | "READY_TO_VERIFY"
    | "VERIFYING"
    | "RESOLVED"
    | "ABORTED"
    | "FAILED";
  active: boolean;
  case_id: string | null;
  case: DemoCase | null;
}

export interface DemoTimelineEvent {
  sequence: number;
  timestamp: string;
  phase: string;
  state: "COMPLETE" | "FAILED" | "RUNNING";
  title: string;
  detail: string;
  service: string | null;
  evidence_event_type: string | null;
}

export interface DemoCase {
  case_id: string;
  run_id: string;
  request_id: string;
  trace_id: string;
  fault_id: string;
  scenario: DemoFault;
  status:
    | "QUEUED"
    | "INJECTING"
    | "FAULT_ACTIVE"
    | "TRANSACTION_RUNNING"
    | "AWAITING_REVIEW"
    | "REMEDIATING"
    | "READY_TO_VERIFY"
    | "VERIFYING"
    | "RESOLVED"
    | "ABORTED"
    | "FAILED";
  phase: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  activation_id: string | null;
  fault_injected_at: string | null;
  transaction_result: {
    request_id: string;
    trace_id: string;
    status_code: number | null;
    latency_ms: number;
    started_at?: string;
    ended_at?: string;
    succeeded: boolean;
    body: Record<string, unknown>;
    transport_error?: string;
  } | null;
  supporting_requests?: Array<{
    request_id: string;
    trace_id: string;
    status_code: number | null;
    latency_ms: number;
    started_at: string;
    ended_at: string;
    succeeded: boolean;
  }>;
  recovery_result: {
    request_id: string;
    trace_id: string;
    status_code: number | null;
    latency_ms: number;
    started_at?: string;
    ended_at?: string;
    succeeded: boolean;
    body: Record<string, unknown>;
  } | null;
  model_analysis: Incident | null;
  model_analysis_note?: string | null;
  first_failure: TelemetryEvent | null;
  timeline: DemoTimelineEvent[];
  evidence: TelemetryEvent[];
  recovery_verified: boolean;
  review: {
    required: boolean;
    status: "NOT_READY" | "PENDING" | "APPROVED" | "TIMED_OUT";
    requested_at: string | null;
    approved_at: string | null;
    approved_by: string | null;
    timeout_seconds: number;
    automatic_cleanup: boolean;
  };
  error: string | null;
}

export interface DemoCaseList {
  count: number;
  cases: DemoCase[];
}

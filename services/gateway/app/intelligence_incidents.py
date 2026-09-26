from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any

from .intelligence_artifacts import FrozenIntelligenceArtifacts
from .intelligence_inference import (
    IntelligenceInferenceRequest,
    run_intelligence_inference,
)
from .intelligence_live import LiveTelemetryBuffer, LiveWindowNotReady
from .intelligence_localization import (
    LiveIntervalEvidence,
    rank_live_origins,
)

SEVERITY_WEIGHTS = {
    "failure_impact": 0.40,
    "latency_impact": 0.25,
    "affected_service_breadth": 0.20,
    "anomaly_persistence": 0.15,
}
SEVERITY_BANDS = (
    (0.25, "LOW"),
    (0.50, "MEDIUM"),
    (0.75, "HIGH"),
    (1.01, "CRITICAL"),
)
SERVICE_NAMES = ("gateway", "authentication", "transaction", "payment", "account", "ledger")
BASELINE_WINDOW_SECONDS = 30
TEMPORAL_STRIDE_SECONDS = 10
TEMPORAL_PERSISTENCE_SAMPLES = 6
RECOVERY_CONFIRMATION_WINDOWS = 3
MAX_INCIDENT_HISTORY = 100


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("incident timestamp must include timezone")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _feature(row: dict[str, float], name: str) -> float:
    return float(row.get(name, 0.0))


def severity_band(score: float) -> str:
    clipped = _clip01(score)
    for upper, name in SEVERITY_BANDS:
        if clipped < upper:
            return name
    return "CRITICAL"


def severity_from_feature_windows(
    baseline_rows: list[dict[str, float]],
    incident_rows: list[dict[str, float]],
    anomaly_flags: list[bool],
) -> dict[str, Any]:
    if not baseline_rows or not incident_rows:
        raise ValueError("live severity requires baseline and incident feature windows")
    if len(incident_rows) != len(anomaly_flags):
        raise ValueError("incident feature windows and anomaly flags are not aligned")

    failure_values = [
        max(
            _feature(row, "request_unexpected_outcome_rate"),
            _feature(row, "request_server_error_rate"),
            _feature(row, "request_transport_error_rate"),
            _feature(row, "event_failure_rate"),
            _feature(row, "event_dependency_failure_rate"),
        )
        for row in incident_rows
    ]
    failure = _clip01(_mean(failure_values))

    base_request = _mean(
        [_feature(row, "request_latency_p95_ms") for row in baseline_rows]
    )
    current_request = _mean(
        [_feature(row, "request_latency_p95_ms") for row in incident_rows]
    )
    base_event = _mean([_feature(row, "event_latency_p95_ms") for row in baseline_rows])
    current_event = _mean(
        [_feature(row, "event_latency_p95_ms") for row in incident_rows]
    )

    def normalized_ratio(current: float, baseline: float) -> float:
        if current <= baseline:
            return 0.0
        ratio = current / max(baseline, 1.0)
        return _clip01((ratio - 1.0) / 4.0)

    latency = max(
        normalized_ratio(current_request, base_request),
        normalized_ratio(current_event, base_event),
    )

    affected: list[str] = []
    for service in SERVICE_NAMES:
        failure_name = f"event_service_{service}_failure_rate"
        latency_name = f"event_service_{service}_latency_p95_ms"
        base_failure = _mean([_feature(row, failure_name) for row in baseline_rows])
        current_failure = _mean([_feature(row, failure_name) for row in incident_rows])
        base_latency = _mean([_feature(row, latency_name) for row in baseline_rows])
        current_latency = _mean([_feature(row, latency_name) for row in incident_rows])
        failure_delta = max(0.0, current_failure - base_failure)
        latency_ratio = (
            current_latency / max(base_latency, 1.0) if current_latency > 0 else 0.0
        )
        if failure_delta >= 0.15 or latency_ratio >= 2.0:
            affected.append(service)
    breadth = _clip01(len(affected) / max(1, len(SERVICE_NAMES)))
    persistence = _clip01(sum(anomaly_flags) / len(anomaly_flags))
    components = {
        "failure_impact": failure,
        "latency_impact": latency,
        "affected_service_breadth": breadth,
        "anomaly_persistence": persistence,
    }
    score = sum(SEVERITY_WEIGHTS[name] * value for name, value in components.items())
    return {
        "score": round(_clip01(score), 6),
        "level": severity_band(score),
        "components": {name: round(value, 6) for name, value in components.items()},
        "affected_services": affected,
    }


@dataclass
class _IncidentRuntime:
    public: dict[str, Any]
    signal_started_at: datetime
    baseline_started_at: datetime | None = None
    baseline_feature_rows: list[dict[str, float]] = field(default_factory=list)
    baseline_events: tuple[dict[str, Any], ...] = ()
    incident_feature_rows: deque[dict[str, float]] = field(
        default_factory=lambda: deque(maxlen=TEMPORAL_PERSISTENCE_SAMPLES)
    )
    anomaly_flags: deque[bool] = field(
        default_factory=lambda: deque(maxlen=TEMPORAL_PERSISTENCE_SAMPLES)
    )


class LiveIncidentManager:
    def __init__(self) -> None:
        self._active: _IncidentRuntime | None = None
        self._history: deque[dict[str, Any]] = deque(maxlen=MAX_INCIDENT_HISTORY)
        self._counter = 0
        self._last_temporal_bucket: int | None = None
        self._last_evaluated_watermark: datetime | None = None
        self._lock = RLock()

    def evaluate(
        self,
        buffer: LiveTelemetryBuffer,
        artifacts: FrozenIntelligenceArtifacts,
    ) -> dict[str, Any]:
        temporal_window = buffer.snapshot(10, require_client_requests=True)
        temporal_end = _parse_timestamp(str(temporal_window["window_ended_at"]))
        bucket = int(temporal_end.timestamp() // TEMPORAL_STRIDE_SECONDS)
        with self._lock:
            if self._last_evaluated_watermark == temporal_end:
                return {
                    "status": "NO_NEW_WINDOW",
                    "incident": self.current_incident(),
                }

            same_temporal_bucket = self._last_temporal_bucket == bucket
            self._last_evaluated_watermark = temporal_end
            if same_temporal_bucket:
                main_result: dict[str, Any] | None = None
                try:
                    main_window = buffer.snapshot(60, require_client_requests=True)
                    main_result = run_intelligence_inference(
                        artifacts,
                        IntelligenceInferenceRequest(
                            window_seconds=60,
                            features=main_window["features"],
                        ),
                    )
                except LiveWindowNotReady:
                    pass

                if self._active is not None:
                    public = self._active.public
                    public["updated_at"] = _iso(temporal_end)
                    if main_result is not None:
                        public["latest_main_status"] = main_result["status"]
                        if main_result["status"] == "ABNORMAL":
                            public["classification"] = deepcopy(
                                main_result["classification"]
                            )
                    self._update_impact_and_localization(
                        self._active,
                        buffer,
                        temporal_end,
                    )
                    return {
                        "status": "INCIDENT_REFRESHED",
                        "temporal": None,
                        "main": main_result,
                        "incident": deepcopy(public),
                    }

                return {
                    "status": "NO_NEW_TEMPORAL_WINDOW",
                    "temporal": None,
                    "main": main_result,
                    "incident": None,
                }

            self._last_temporal_bucket = bucket

            temporal_result = run_intelligence_inference(
                artifacts,
                IntelligenceInferenceRequest(
                    window_seconds=10,
                    features=temporal_window["features"],
                ),
            )
            main_window: dict[str, Any] | None = None
            main_result: dict[str, Any] | None = None
            try:
                main_window = buffer.snapshot(60, require_client_requests=True)
                main_result = run_intelligence_inference(
                    artifacts,
                    IntelligenceInferenceRequest(
                        window_seconds=60,
                        features=main_window["features"],
                    ),
                )
            except LiveWindowNotReady:
                pass

            temporal_abnormal = temporal_result["status"] == "ABNORMAL_SIGNAL"
            main_abnormal = bool(main_result and main_result["status"] == "ABNORMAL")
            opened_now = False
            if self._active is None and (temporal_abnormal or main_abnormal):
                source_window = temporal_window if temporal_abnormal else main_window
                assert source_window is not None
                signal_started_at = _parse_timestamp(
                    str(source_window["window_started_at"])
                )
                self._active = self._open_incident(
                    buffer,
                    detected_at=temporal_end,
                    signal_started_at=signal_started_at,
                    detection_source=("temporal_10s" if temporal_abnormal else "main_60s"),
                )
                opened_now = True

            if self._active is None:
                return {
                    "status": "NORMAL",
                    "temporal": temporal_result,
                    "main": main_result,
                    "incident": None,
                }

            runtime = self._active
            runtime.incident_feature_rows.append(
                dict(temporal_window["features"])
            )
            runtime.anomaly_flags.append(temporal_abnormal)
            public = runtime.public
            public["updated_at"] = _iso(temporal_end)
            public["temporal"] = {
                "latest_status": temporal_result["status"],
                "latest_anomaly": temporal_result["anomaly"],
                "samples_last_60s": len(runtime.anomaly_flags),
                "anomalous_samples_last_60s": sum(runtime.anomaly_flags),
                "consecutive_normal_windows": public["temporal"].get(
                    "consecutive_normal_windows", 0
                ),
            }

            if main_result is not None:
                public["latest_main_status"] = main_result["status"]
                if main_result["status"] == "ABNORMAL":
                    public["classification"] = deepcopy(main_result["classification"])

            self._update_impact_and_localization(
                runtime,
                buffer,
                temporal_end,
            )

            healthy_now = not temporal_abnormal and not main_abnormal
            if healthy_now:
                public["temporal"]["consecutive_normal_windows"] += 1
            else:
                public["temporal"]["consecutive_normal_windows"] = 0

            recovery_count = public["temporal"]["consecutive_normal_windows"]
            main_is_normal = public.get("latest_main_status") == "NORMAL"
            if recovery_count >= RECOVERY_CONFIRMATION_WINDOWS and main_is_normal:
                resolved = self._resolve_active(temporal_end)
                return {
                    "status": "INCIDENT_RESOLVED",
                    "temporal": temporal_result,
                    "main": main_result,
                    "incident": resolved,
                }

            return {
                "status": "INCIDENT_OPENED" if opened_now else "INCIDENT_UPDATED",
                "temporal": temporal_result,
                "main": main_result,
                "incident": deepcopy(public),
            }

    def _open_incident(
        self,
        buffer: LiveTelemetryBuffer,
        *,
        detected_at: datetime,
        signal_started_at: datetime,
        detection_source: str,
    ) -> _IncidentRuntime:
        self._counter += 1
        incident_id = f"inc-{detected_at.strftime('%Y%m%dT%H%M%S')}-{self._counter:04d}"
        baseline_rows: list[dict[str, float]] = []
        baseline_events: tuple[dict[str, Any], ...] = ()
        baseline_started_at = signal_started_at - timedelta(
            seconds=BASELINE_WINDOW_SECONDS
        )
        baseline_available = True
        try:
            for offset in (20, 10, 0):
                ended_at = signal_started_at - timedelta(seconds=offset)
                snapshot = buffer.snapshot_ending_at(
                    10,
                    ended_at,
                    start_tolerance_seconds=1.0,
                    require_client_requests=True,
                )
                baseline_rows.append(dict(snapshot["features"]))
            baseline_events = buffer.events_between(
                baseline_started_at,
                signal_started_at,
                start_tolerance_seconds=1.0,
            )
        except LiveWindowNotReady:
            baseline_available = False
            baseline_rows.clear()
            baseline_events = ()

        public = {
            "incident_id": incident_id,
            "state": "ACTIVE",
            "detected_at": _iso(detected_at),
            "updated_at": _iso(detected_at),
            "resolved_at": None,
            "detection_source": detection_source,
            "signal_started_at": _iso(signal_started_at),
            "latest_main_status": None,
            "classification": None,
            "localization": None,
            "severity": None,
            "baseline": {
                "available": baseline_available,
                "window_seconds": BASELINE_WINDOW_SECONDS,
                "window_started_at": (
                    _iso(baseline_started_at) if baseline_available else None
                ),
                "window_ended_at": (
                    _iso(signal_started_at) if baseline_available else None
                ),
            },
            "temporal": {
                "latest_status": None,
                "latest_anomaly": None,
                "samples_last_60s": 0,
                "anomalous_samples_last_60s": 0,
                "consecutive_normal_windows": 0,
            },
        }
        return _IncidentRuntime(
            public=public,
            signal_started_at=signal_started_at,
            baseline_started_at=(baseline_started_at if baseline_available else None),
            baseline_feature_rows=baseline_rows,
            baseline_events=baseline_events,
        )

    def _update_impact_and_localization(
        self,
        runtime: _IncidentRuntime,
        buffer: LiveTelemetryBuffer,
        ended_at: datetime,
    ) -> None:
        if runtime.baseline_feature_rows:
            runtime.public["severity"] = severity_from_feature_windows(
                runtime.baseline_feature_rows,
                list(runtime.incident_feature_rows),
                list(runtime.anomaly_flags),
            )
        if runtime.baseline_started_at is None or not runtime.baseline_events:
            return
        current_started_at = max(
            runtime.signal_started_at,
            ended_at - timedelta(seconds=60),
        )
        try:
            current_events = buffer.events_between(current_started_at, ended_at)
        except LiveWindowNotReady:
            return
        ranking = rank_live_origins(
            LiveIntervalEvidence(runtime.baseline_events, runtime.baseline_started_at),
            LiveIntervalEvidence(current_events, current_started_at),
        )
        top_three = deepcopy(ranking[:3])
        runtime.public["localization"] = {
            "method": "phase12c_causal_observable_ranking",
            "probable_origin": top_three[0]["candidate"] if top_three else None,
            "top_3": top_three,
        }

    def _resolve_active(self, resolved_at: datetime) -> dict[str, Any]:
        assert self._active is not None
        public = self._active.public
        public["state"] = "RESOLVED"
        public["resolved_at"] = _iso(resolved_at)
        public["updated_at"] = _iso(resolved_at)
        detected_at = _parse_timestamp(public["detected_at"])
        public["active_duration_seconds"] = round(
            max(0.0, (resolved_at - detected_at).total_seconds()),
            6,
        )
        resolved = deepcopy(public)
        self._history.append(resolved)
        self._active = None
        return resolved

    def current_incident(self) -> dict[str, Any] | None:
        with self._lock:
            return deepcopy(self._active.public) if self._active is not None else None

    def incidents(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(reversed(self._history))
            if self._active is not None:
                rows.insert(0, deepcopy(self._active.public))
            return deepcopy(rows)

    def incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._active is not None and self._active.public["incident_id"] == incident_id:
                return deepcopy(self._active.public)
            for item in reversed(self._history):
                if item["incident_id"] == incident_id:
                    return deepcopy(item)
        return None


_live_incident_manager = LiveIncidentManager()


def get_live_incident_manager() -> LiveIncidentManager:
    return _live_incident_manager

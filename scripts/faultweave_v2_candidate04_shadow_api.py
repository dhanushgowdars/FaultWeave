"""Research-only localhost FastAPI service for frozen FaultWeave Candidate 04.

No Docker changes, fault injection, model fitting, file writes or gateway route changes.
Launch with: python -m scripts.faultweave_v2_candidate04_shadow_api --serve
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, field_validator

MODEL_LABEL = "v2-verified126-candidate-04"
RESEARCH_STATUS = "RESEARCH_ONLY_NOT_DEPLOYED"
RUN_ID_RE = re.compile(r"^v2blind-[0-9a-f]{16}$")
REPO_ROOT = Path(__file__).resolve().parents[1]
SCORES_ROOT = REPO_ROOT / "data/datasets/v2/evaluation-only/candidate04"

app = FastAPI(
    title="FaultWeave Candidate 04 — Shadow Inference (Research Only)",
    description=(
        "Local non-production API for one frozen model. No gateway integration, "
        "live collector, incident localization or fault-control capability."
    ),
    version="0.1.0",
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: Literal[60]
    features: dict[str, float]

    @field_validator("window_seconds", mode="before")
    @classmethod
    def strict_window(cls, value: Any) -> Any:
        if type(value) is not int or value != 60:
            raise ValueError("window_seconds must be the integer 60")
        return value

    @field_validator("features", mode="before")
    @classmethod
    def reject_bad_types(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ValueError("features must be an object keyed by the 65 frozen feature names")
        if any(type(val) not in (int, float) for val in data.values()):
            raise ValueError("features must be numeric; booleans and numeric strings are forbidden")
        if any(not math.isfinite(val) for val in data.values()):
            raise ValueError("features must contain only finite numbers")
        return data


def _feature_names() -> tuple[str, ...]:
    # Import the EXACT schema from the project; never maintain a second schema.
    from datasets.final_features import FEATURE_NAMES

    names = tuple(FEATURE_NAMES)
    if len(names) != 65 or len(set(names)) != 65:
        raise RuntimeError("frozen feature schema is not the expected 65 unique columns")
    return names


def _frozen_artifacts() -> tuple[Path, str]:
    """Check frozen code, bundle, report and research dataset on EACH request."""
    from scripts.faultweave_v2_candidate04_blind_eval import (
        EXPECTED_HASHES,
        MODEL_PATH,
        ROOT,
        require_regular,
        sha256,
    )

    for path, expected in EXPECTED_HASHES.items():
        require_regular(path, ROOT)
        if sha256(path).lower() != expected.lower():
            raise RuntimeError("STOP: frozen Candidate 04 input has changed")
    return MODEL_PATH, EXPECTED_HASHES[MODEL_PATH]


@lru_cache(maxsize=1)
def _load_predictor() -> tuple[Any, Any, Any]:
    """Only deserialize a fingerprint-checked model from a fixed trusted path."""
    import joblib
    from scripts.faultweave_v2_candidate04 import predict

    model_path, _ = _frozen_artifacts()
    bundle = joblib.load(model_path)
    if list(bundle.get("feature_names", [])) != list(_feature_names()):
        raise RuntimeError("frozen model feature order does not match the schema")
    if not isinstance(bundle.get("model"), dict) or not isinstance(bundle.get("isolation"), dict):
        raise RuntimeError("frozen Candidate 04 bundle has invalid model structure")
    return predict, bundle["model"], bundle["isolation"]


def _preflight() -> dict[str, Any]:
    _, model_hash = _frozen_artifacts()
    feature_names = _feature_names()
    _load_predictor()
    return {
        "status": "READY_FOR_RESEARCH_INFERENCE",
        "deployment_status": RESEARCH_STATUS,
        "model": MODEL_LABEL,
        "model_sha256": model_hash,
        "window_seconds": 60,
        "feature_count": len(feature_names),
        "feature_schema_sha256": hashlib.sha256("\n".join(feature_names).encode()).hexdigest(),
        "gateway_replaced": False,
        "isolation_or_fault_control_enabled": False,
        "ground_truth_used_in_inference": False,
    }


def _infer(features: dict[str, float]) -> dict[str, Any]:
    ready = _preflight()
    names = _feature_names()
    missing = sorted(set(names) - set(features))
    extra = sorted(set(features) - set(names))
    if missing or extra:
        raise ValueError(f"expected exactly 65 named features (missing={missing}; extra={extra})")
    ordered = {name: float(features[name]) for name in names}
    predict, model, iso = _load_predictor()
    decisions = predict(model, iso, [{"features": ordered}])
    if not isinstance(decisions, list) or len(decisions) != 1:
        raise RuntimeError("model did not return exactly one decision")
    decision = decisions[0]
    allowed = (
        "prediction", "reason", "binary_fault", "iso_anomaly",
        "nearest_predicted_class", "known_distance", "known_radius",
        "normal_distance", "normal_radius", "isolation_score",
    )
    if not isinstance(decision, dict) or any(key not in decision for key in allowed):
        raise RuntimeError("model decision format mismatch")
    public_decision = {key: decision[key] for key in allowed}
    # Prevent non-finite values leaking into JSON output.
    if any(type(value) is float and not math.isfinite(value) for value in public_decision.values()):
        raise RuntimeError("nonfinite model diagnostics")
    return {
        "status": "INFERRED_RESEARCH_ONLY",
        "deployment_status": RESEARCH_STATUS,
        "model": MODEL_LABEL,
        "model_sha256": ready["model_sha256"],
        "window_seconds": 60,
        "feature_schema_sha256": ready["feature_schema_sha256"],
        "feature_count": len(ordered),
        "decision": public_decision,
        "input_provenance": "OPERATOR_SUBMITTED_FEATURES_NOT_INDEPENDENTLY_AUDITED",
        "probable_origin": None,
        "origin_status": "NOT_ESTIMATED_BY_CANDIDATE04",
        "automatic_recovery": False,
        "training_performed": False,
    }


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("missing or symlinked evaluation receipt")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise RuntimeError("invalid evaluation receipt JSON")
    return obj


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(piece)
        return digest.hexdigest()


def _verified_evaluations() -> dict[str, Any]:
    """Read committed scores only; never read raw/hidden ground-truth artifacts."""
    _, model_hash = _frozen_artifacts()
    if not SCORES_ROOT.is_dir() or SCORES_ROOT.is_symlink():
        return {"count": 0, "correct_unknown": 0, "results": []}
    rows: list[dict[str, Any]] = []
    for score_path in sorted(SCORES_ROOT.glob("*.score.json")):
        run_id = score_path.name.removesuffix(".score.json")
        if not RUN_ID_RE.fullmatch(run_id):
            raise RuntimeError("unexpected evaluation score filename")
        receipt_path = SCORES_ROOT / f"{run_id}.prediction.json"
        score = _read_object(score_path)
        receipt = _read_object(receipt_path)
        if (score.get("schema_version") != "faultweave-candidate04-evaluation-reveal.1"
                or receipt.get("schema_version") != "faultweave-candidate04-evaluation-prediction.1"
                or score.get("run_id") != run_id or receipt.get("run_id") != run_id
                or score.get("model_sha256", "").lower() != model_hash.lower()
                or receipt.get("model_sha256", "").lower() != model_hash.lower()
                or score.get("prediction_sha256", "").lower() != _sha(receipt_path)
                or score.get("prediction") != (receipt.get("decision") or {}).get("prediction")
                or score.get("unknown_rejected") is not (score.get("prediction") == "UNKNOWN ABNORMAL PATTERN")
                or score.get("recovery_verified") is not True):
            raise RuntimeError("evaluation evidence verification failed; refusing to serve results")
        rows.append({
            "run_id": run_id,
            "actual_family": score.get("actual_fault_family"),
            "prediction": score.get("prediction"),
            "unknown_rejected": score["unknown_rejected"],
            "recovery_verified": True,
            "prediction_sha256": _sha(receipt_path),
            "evaluation_only": True,
        })
    return {
        "count": len(rows),
        "correct_unknown": sum(row["unknown_rejected"] for row in rows),
        "results": rows,
        "scope": "COMMITTED_SEALED_UNKNOWN_EVALUATIONS_ONLY",
        "generalization_claim": "INSUFFICIENT_SAMPLE_SIZE",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "up", "type": "research_shadow_service"}


@app.get("/api/v2/candidate04/ready")
def ready() -> dict[str, Any]:
    try:
        return _preflight()
    except (OSError, RuntimeError, ValueError, ImportError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Frozen Candidate 04 not ready ({type(exc).__name__})") from exc


@app.get("/api/v2/candidate04/schema")
def schema() -> dict[str, Any]:
    try:
        ready_info = _preflight()
        return {**ready_info, "feature_names": list(_feature_names())}
    except (OSError, RuntimeError, ValueError, ImportError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Frozen Candidate 04 not ready ({type(exc).__name__})") from exc


@app.post("/api/v2/candidate04/infer")
def infer(payload: InferenceRequest) -> dict[str, Any]:
    try:
        return _infer(payload.features)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (OSError, RuntimeError, ImportError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=f"Frozen Candidate 04 inference unavailable ({type(exc).__name__})") from exc


@app.get("/api/v2/candidate04/evaluations")
def evaluations() -> dict[str, Any]:
    try:
        return {
            "model": MODEL_LABEL,
            "deployment_status": RESEARCH_STATUS,
            **_verified_evaluations(),
        }
    except (OSError, RuntimeError, ValueError, ImportError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=f"Evaluation evidence unavailable ({type(exc).__name__})") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--preflight", action="store_true")
    choice.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    ready_info = _preflight()
    if args.preflight:
        print("PASS: Candidate 04 frozen model loaded; 65-feature contract verified")
        print("MODEL SHA256:", ready_info["model_sha256"])
        print("NO Docker interaction, model writes, fault injection, or traffic sent")
    else:
        import uvicorn

        print("Research-only shadow API: http://127.0.0.1:28190/docs", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=28190, workers=1, reload=False)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        print(f"STOP: {exc}")
        raise SystemExit(1)

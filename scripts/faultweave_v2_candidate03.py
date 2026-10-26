"""FaultWeave V2 Candidate 03: guarded, training-only calibrated open-set research.

Run from the repository root:
    python -m scripts.faultweave_v2_candidate03

Creates a new *non-deployed* candidate. Does not update captures, earlier
models, the FastAPI backend, or the reserved test split.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, IsolationForest
from sklearn.metrics import accuracy_score, f1_score, pairwise_distances
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from datasets.final_features import FEATURE_NAMES
from scripts.faultweave_v2_fasttrack import verify_feature_rows

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "datasets" / "v2"
FEATURE_PATH = BASE / "assembled" / "verified-126-v1" / "features.jsonl"
RECEIPT_PATH = BASE / "assembled" / "verified-126-v1" / "admission_book.json"
OUTPUT = BASE / "models" / "v2-verified126-candidate-03"
EXPECTED_SHA = "dfa6d33c909863db76261184f4e8bad6b7fe23597e67994cee055e397a90865c"
SEED = 42
# Research-only tolerance. Neither this nor any other threshold is a
# demonstrated production-safe open-set operating point.
CLASS_RADIUS_MULTIPLIER = 1.25
NORMAL_RADIUS_MULTIPLIER = 1.25
NORMAL_OOF_QUANTILE = 0.95
FEATURES = tuple(FEATURE_NAMES)


def to_matrix(rows: list[dict]) -> np.ndarray:
    x = np.asarray(
        [[row["features"][name] for name in FEATURES] for row in rows],
        dtype=np.float64,
    )
    if x.ndim != 2 or x.shape[1] != len(FEATURES) or not np.isfinite(x).all():
        raise RuntimeError("STOP: malformed or nonfinite feature matrix")
    return x


def nearest_other_distances(points: np.ndarray) -> np.ndarray:
    if len(points) < 2:
        raise RuntimeError("STOP: insufficient samples for reference radii")
    d = pairwise_distances(points)
    np.fill_diagonal(d, np.inf)
    return d.min(axis=1)


def train_only_normal_iso(normal_rows: list[dict]):
    """Fit on normal TRAIN ONLY; calibrate cutoff using normal LOO scores."""
    normal_x = to_matrix(normal_rows)
    if len(normal_x) != 12:
        raise RuntimeError("STOP: expected 12 training NORMAL examples")

    oof_scores = []
    for held in range(len(normal_x)):
        mask = np.arange(len(normal_x)) != held
        scaler = StandardScaler().fit(normal_x[mask])
        forest = IsolationForest(n_estimators=200, random_state=20261902, n_jobs=-1)
        forest.fit(scaler.transform(normal_x[mask]))
        held_score = -forest.decision_function(scaler.transform(normal_x[held:held + 1]))
        oof_scores.append(float(held_score[0]))

    cutoff = float(np.quantile(oof_scores, NORMAL_OOF_QUANTILE))
    scaler = StandardScaler().fit(normal_x)
    forest = IsolationForest(n_estimators=200, random_state=20261902, n_jobs=-1)
    forest.fit(scaler.transform(normal_x))
    return {
        "scaler": scaler,
        "forest": forest,
        "threshold": cutoff,
        "normal_loo_scores": oof_scores,
    }


def train_known_model(train_rows: list[dict]):
    x_all = to_matrix(train_rows)
    y_binary = np.asarray([int(r["label"] != "NORMAL") for r in train_rows])
    if len(set(y_binary)) != 2:
        raise RuntimeError("STOP: binary detector needs normal and known faults")

    binary = ExtraTreesClassifier(
        n_estimators=300, class_weight="balanced", random_state=SEED, n_jobs=-1
    )
    binary.fit(x_all, y_binary)

    faults = [r for r in train_rows if r["label"] != "NORMAL"]
    x_fault = to_matrix(faults)
    y_fault = np.asarray([r["label"] for r in faults])
    if any(np.sum(y_fault == name) < 3 for name in set(y_fault)):
        raise RuntimeError("STOP: too few samples per known fault family")

    classifier = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=3))
    classifier.fit(x_fault, y_fault)
    fault_scaler = classifier.named_steps["standardscaler"]
    reference = fault_scaler.transform(x_fault)

    radii = {}
    for name in sorted(set(y_fault)):
        points = reference[y_fault == name]
        radius = np.quantile(nearest_other_distances(points), 0.95)
        radii[name] = max(float(radius) * CLASS_RADIUS_MULTIPLIER, 1e-8)

    normal_rows = [r for r in train_rows if r["label"] == "NORMAL"]
    global_scaler = StandardScaler().fit(x_all)
    normal_reference = global_scaler.transform(to_matrix(normal_rows))
    normal_radius = max(
        float(np.quantile(nearest_other_distances(normal_reference), 0.95))
        * NORMAL_RADIUS_MULTIPLIER,
        1e-8,
    )

    return {
        "binary": binary,
        "classifier": classifier,
        "fault_scaler": fault_scaler,
        "fault_reference": reference,
        "fault_labels": y_fault,
        "class_radii": radii,
        "global_scaler": global_scaler,
        "normal_reference": normal_reference,
        "normal_radius": normal_radius,
    }


def predict(model, iso, rows: list[dict]) -> list[dict]:
    x = to_matrix(rows)
    binary_pred = model["binary"].predict(x)
    known_pred = model["classifier"].predict(x)
    query = model["fault_scaler"].transform(x)
    normal_query = model["global_scaler"].transform(x)
    iso_scores = -iso["forest"].decision_function(iso["scaler"].transform(x))
    outcomes = []

    for i, name in enumerate(known_pred):
        in_class = model["fault_reference"][model["fault_labels"] == name]
        distance = float(pairwise_distances(query[i:i+1], in_class).min())
        far_from_class = distance > model["class_radii"][name]
        normal_distance = float(
            pairwise_distances(normal_query[i:i+1], model["normal_reference"]).min()
        )
        far_from_normal = normal_distance > model["normal_radius"]
        iso_flag = bool(iso_scores[i] >= iso["threshold"])

        if binary_pred[i] == 1:
            decision = "UNKNOWN ABNORMAL PATTERN" if far_from_class else str(name)
            reason = "KNOWN_FAULT_OUTSIDE_CLASS_SUPPORT" if far_from_class else "KNOWN_FAULT_ACCEPTED"
        elif iso_flag and far_from_class and far_from_normal:
            decision = "UNKNOWN ABNORMAL PATTERN"
            reason = "ISO_NOVELTY_RESCUE_OUTSIDE_NORMAL_AND_KNOWN_SUPPORT"
        else:
            decision = "NORMAL"
            reason = "NORMAL_OR_INSUFFICIENT_NOVELTY_EVIDENCE"

        outcomes.append({
            "prediction": decision,
            "reason": reason,
            "binary_fault": bool(binary_pred[i] == 1),
            "iso_anomaly": iso_flag,
            "nearest_predicted_class": str(name),
            "known_distance": distance,
            "known_radius": model["class_radii"][name],
            "normal_distance": normal_distance,
            "normal_radius": model["normal_radius"],
            "isolation_score": float(iso_scores[i]),
        })
    return outcomes


def main():
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("STOP: Candidate 03 output already exists; do not overwrite")
    if not OUTPUT.parent.is_dir() or OUTPUT.parent.is_symlink():
        raise RuntimeError("STOP: candidate models parent directory is unsafe")

    ready = verify_feature_rows(ROOT, FEATURE_PATH, RECEIPT_PATH)
    if (ready.get("status") != "READY_FOR_CANDIDATE_TRAINING"
            or ready.get("source_feature_sha256") != EXPECTED_SHA
            or ready.get("feature_rows") != 126
            or ready.get("independent_runs") != 126):
        raise RuntimeError("STOP: assembled dataset / independent admissions mismatch")
    if len(FEATURES) != 65 or len(set(FEATURES)) != len(FEATURES):
        raise RuntimeError("STOP: frozen feature schema mismatch")

    with FEATURE_PATH.open(encoding="utf-8") as fd:
        rows = [json.loads(line) for line in fd if line.strip()]
    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "validation"]
    # Deliberately NEVER evaluate test vectors during research/model selection.
    test_count = sum(r["split"] == "test" for r in rows)
    if (len(train), len(val), test_count) != (84, 21, 21):
        raise RuntimeError("STOP: unexpected train/validation/test split counts")
    tr_counts = Counter(r["label"] for r in train)
    va_counts = Counter(r["label"] for r in val)
    families = sorted(label for label in tr_counts if label != "NORMAL")
    if (len(families) != 9 or tr_counts["NORMAL"] != 12
            or va_counts["NORMAL"] != 3
            or any(tr_counts[k] != 8 or va_counts[k] != 2 for k in families)):
        raise RuntimeError("STOP: unexpected class distribution")

    iso = train_only_normal_iso([r for r in train if r["label"] == "NORMAL"])
    model = train_known_model(train)
    val_out = predict(model, iso, val)
    actual = [r["label"] for r in val]
    predicted = [p["prediction"] for p in val_out]
    errors = [
        {"actual": actual[i], "predicted": predicted[i], "reason": val_out[i]["reason"]}
        for i in range(len(val)) if actual[i] != predicted[i]
    ]

    simulated_unknowns = {}
    for hidden in families:
        remaining = [r for r in train if r["label"] != hidden]
        hidden_rows = [r for r in train if r["label"] == hidden]
        # Normal-only IsolationForest/calibration is held fixed: the excluded
        # family never entered its fit or threshold selection.
        leave_out_model = train_known_model(remaining)
        decisions = predict(leave_out_model, iso, hidden_rows)
        simulated_unknowns[hidden] = sum(
            p["prediction"] == "UNKNOWN ABNORMAL PATTERN" for p in decisions
        )

    report = {
        "status": "CANDIDATE_03_OPEN_SET_RESEARCH_ONLY_NOT_DEPLOYED",
        "dataset_sha256": EXPECTED_SHA,
        "train_rows": len(train),
        "validation_rows": len(val),
        "test_rows_reserved_not_evaluated": test_count,
        "feature_count": len(FEATURES),
        "model_components": ["ExtraTrees binary", "3NN known", "IsolationForest normal"],
        "calibration": {
            "isolation_threshold_source": "normal-training leave-one-out, quantile 0.95",
            "isolation_threshold": iso["threshold"],
            "normal_radius_multiplier": NORMAL_RADIUS_MULTIPLIER,
            "class_radius_multiplier": CLASS_RADIUS_MULTIPLIER,
            "class_radii": model["class_radii"],
            "normal_radius": model["normal_radius"],
        },
        "development_validation": {
            "accuracy": float(accuracy_score(actual, predicted)),
            "macro_f1": float(f1_score(actual, predicted, average="macro", zero_division=0)),
            "correct": int(sum(a == b for a, b in zip(actual, predicted))),
            "total": len(val),
            "known_fault_false_unknowns": sum(
                a not in ("NORMAL", "UNKNOWN ABNORMAL PATTERN")
                and p == "UNKNOWN ABNORMAL PATTERN"
                for a, p in zip(actual, predicted)
            ),
            "normal_false_alarms": sum(a == "NORMAL" and p != "NORMAL" for a, p in zip(actual, predicted)),
            "errors": errors,
            "caveat": "Validation has informed prior model development; NOT an independent final score.",
        },
        "lofo_simulated_unknowns_training_only": {
            "by_family": simulated_unknowns,
            "rejected": int(sum(simulated_unknowns.values())),
            "total": 72,
            "caveat": "Withheld known faults are proxies, not sealed novel fault families.",
        },
        "sealed_unknown_evaluated": False,
        "production_activation_authorized": False,
    }

    # After ALL validation and model fitting, create a NEW candidate directory.
    # Earlier models, captures, and source plans are never overwritten.
    OUTPUT.mkdir(exist_ok=False)
    joblib.dump(
        {"model": model, "isolation": iso, "feature_names": list(FEATURES),
         "decision_policy_version": "candidate03-open-set-research-v1"},
        OUTPUT / "candidate03_bundle.joblib",
    )
    (OUTPUT / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("\nCandidate 03 saved:", OUTPUT)
    print("Not deployed. Test split and sealed unknown scenarios were not evaluated.")


if __name__ == "__main__":
    main()

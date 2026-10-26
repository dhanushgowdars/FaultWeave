import json
from pathlib import Path

import joblib
import numpy as np

from collections import Counter
from sklearn.ensemble import ExtraTreesClassifier, IsolationForest
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score, f1_score

from datasets.final_features import FEATURE_NAMES
from scripts.faultweave_v2_fasttrack import verify_feature_rows


ROOT = Path(__file__).resolve().parents[1]

BASE = ROOT / "data/datasets/v2"

FEATURES = BASE / "assembled/verified-126-v1/features.jsonl"
RECEIPTS = BASE / "assembled/verified-126-v1/admission_book.json"

OUTPUT = BASE / "models/v2-verified126-candidate-02"

EXPECTED_SHA = "dfa6d33c909863db76261184f4e8bad6b7fe23597e67994cee055e397a90865c"


def main():
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("STOP: Candidate 02 output already exists.")

    ready = verify_feature_rows(ROOT, FEATURES, RECEIPTS)

    if ready["status"] != "READY_FOR_CANDIDATE_TRAINING":
        raise RuntimeError("Dataset verification failed.")

    if ready["source_feature_sha256"] != EXPECTED_SHA:
        raise RuntimeError("Source dataset fingerprint changed.")

    with FEATURES.open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "validation"]
    test_count = sum(r["split"] == "test" for r in rows)

    if (len(train), len(val), test_count) != (84, 21, 21):
        raise RuntimeError("Unexpected dataset split.")

    train_counts = Counter(r["label"] for r in train)
    val_counts = Counter(r["label"] for r in val)

    classes = sorted(k for k in train_counts if k != "NORMAL")

    if (
        len(classes) != 9
        or train_counts["NORMAL"] != 12
        or val_counts["NORMAL"] != 3
        or any(train_counts[c] != 8 or val_counts[c] != 2 for c in classes)
    ):
        raise RuntimeError("Unexpected class distribution.")

    names = list(FEATURE_NAMES)

    if len(names) != 65:
        raise RuntimeError("Unexpected feature schema.")

    def vectors(items):
        return np.asarray([[r["features"][n] for n in names] for r in items], dtype=np.float64)

    X_train = vectors(train)
    X_val = vectors(val)

    if not np.isfinite(X_train).all() or not np.isfinite(X_val).all():
        raise RuntimeError("Non-finite feature values.")

    y_binary = np.asarray([int(r["label"] != "NORMAL") for r in train])

    y_val_binary = np.asarray([int(r["label"] != "NORMAL") for r in val])

    binary = ExtraTreesClassifier(
        n_estimators=300, class_weight="balanced", random_state=42, n_jobs=-1
    )

    cv_binary = StratifiedKFold(n_splits=4, shuffle=True, random_state=42)

    binary_oof = cross_val_predict(binary, X_train, y_binary, cv=cv_binary, n_jobs=1)

    binary_cv_accuracy = accuracy_score(y_binary, binary_oof)

    binary.fit(X_train, y_binary)

    binary_val = binary.predict(X_val)

    fault_train = [r for r in train if r["label"] != "NORMAL"]
    fault_val = [r for r in val if r["label"] != "NORMAL"]

    X_fault = vectors(fault_train)
    X_fault_val = vectors(fault_val)

    y_fault = np.asarray([r["label"] for r in fault_train])
    y_fault_val = np.asarray([r["label"] for r in fault_val])

    classifier = make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=3))

    cv_fault = StratifiedKFold(n_splits=4, shuffle=True, random_state=42)

    fault_oof = cross_val_predict(classifier, X_fault, y_fault, cv=cv_fault, n_jobs=1)

    fault_cv_accuracy = accuracy_score(y_fault, fault_oof)

    classifier.fit(X_fault, y_fault)

    fault_val_pred = classifier.predict(X_fault_val)

    raw_val = classifier.predict(X_val)

    final_val = ["NORMAL" if binary_val[i] == 0 else str(raw_val[i]) for i in range(len(val))]

    y_val = [r["label"] for r in val]

    normal_train = [r for r in train if r["label"] == "NORMAL"]
    X_normal = vectors(normal_train)

    normal_scaler = StandardScaler().fit(X_normal)

    iso = IsolationForest(n_estimators=200, random_state=20261902, n_jobs=-1)

    iso.fit(normal_scaler.transform(X_normal))

    results = {
        "status": "CANDIDATE_02_CLOSED_SET_RESEARCH_ONLY",
        "deployed": False,
        "source_feature_sha256": EXPECTED_SHA,
        "feature_count": len(names),
        "training_rows": len(train),
        "validation_rows": len(val),
        "test_rows_reserved": test_count,
        "test_predictions_performed": False,
        "binary_cv_accuracy": float(binary_cv_accuracy),
        "known_fault_cv_accuracy": float(fault_cv_accuracy),
        "binary_validation_accuracy": float(accuracy_score(y_val_binary, binary_val)),
        "known_fault_validation_accuracy": float(accuracy_score(y_fault_val, fault_val_pred)),
        "closed_set_validation_accuracy": float(accuracy_score(y_val, final_val)),
        "closed_set_validation_macro_f1": float(
            f1_score(y_val, final_val, average="macro", zero_division=0)
        ),
        "validation_errors": [
            {"actual": actual, "predicted": predicted}
            for actual, predicted in zip(y_val, final_val)
            if actual != predicted
        ],
        "unknown_rejection": "NOT_IMPLEMENTED",
        "sealed_unknown_evaluation": "NOT_PERFORMED",
        "activation": "NOT_AUTHORIZED",
    }

    # Create a separate candidate only after all checks and fitting.
    OUTPUT.mkdir(parents=False, exist_ok=False)

    joblib.dump({"model": binary, "feature_names": names}, OUTPUT / "binary_extra_trees.joblib")

    joblib.dump(
        {"model": classifier, "feature_names": names, "classes": classes},
        OUTPUT / "known_fault_3nn.joblib",
    )

    joblib.dump(
        {
            "model": iso,
            "scaler": normal_scaler,
            "feature_names": names,
            "role": "ADVISORY_ONLY_NO_CALIBRATED_OPEN_SET_RULE",
        },
        OUTPUT / "isolation_forest.joblib",
    )

    (OUTPUT / "evaluation.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(json.dumps(results, indent=2))
    print("\nCandidate 02 saved:", OUTPUT)
    print("Original dataset and Candidate 01 preserved.")
    print("Not deployed. Test split not evaluated.")


if __name__ == "__main__":
    main()

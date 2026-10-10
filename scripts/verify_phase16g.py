"""Live multi-case quality acceptance for the grounded Groq incident assistant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from experiments.assistant_quality import evaluate_assistant_quality, render_quality_report
from experiments.manifest import write_json
from experiments.transfer_fault_cases import TRANSFER_FAULT_CASES
from scripts.phase16_transfer_fault_case import (
    ROOT,
    client_for_run,
    resume_case,
    start_case,
)

OUTPUT_DIRECTORY = ROOT / "data" / "experiments" / "phase16g-ai-quality"
CHECKPOINT_DIRECTORY = OUTPUT_DIRECTORY / "checkpoints"
RECOVERY_EXPLANATION_CASE = "ledger-service-unavailable"
NORMAL_CASE_ID = "normal-transfer"


def expected_case_keys() -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = [(NORMAL_CASE_ID, "normal")]
    for case_id in TRANSFER_FAULT_CASES:
        values.append((case_id, "awaiting_review"))
        if case_id == RECOVERY_EXPLANATION_CASE:
            values.append((case_id, "recovered"))
    return tuple(values)


def checkpoint_path(case_id: str, lifecycle: str) -> Path:
    safe_case = case_id.replace("/", "-")
    safe_lifecycle = lifecycle.replace("/", "-")
    return CHECKPOINT_DIRECTORY / f"{safe_case}--{safe_lifecycle}.json"


def save_checkpoints(results: list[dict[str, Any]]) -> None:
    CHECKPOINT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for result in results:
        path = checkpoint_path(result["case_id"], result["lifecycle"])
        write_json(path, result)
        print(
            f"CHECKPOINT: {result['case_id']} [{result['lifecycle']}] -> {path}",
            flush=True,
        )


def load_checkpoints() -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    missing: list[str] = []
    for case_id, lifecycle in expected_case_keys():
        path = checkpoint_path(case_id, lifecycle)
        if not path.exists():
            missing.append(f"{case_id} [{lifecycle}]")
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid Phase 16G checkpoint: {path}")
        results.append(value)
    return results, missing


def checked(response: httpx.Response) -> dict[str, Any]:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object from {response.request.url}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def explain(client: httpx.Client, transfer_id: str) -> dict[str, Any]:
    return checked(
        client.post(
            f"/api/v1/transfers/{transfer_id}/assistant/explain",
            json={"focus": "overview"},
        )
    )


def print_preview(case_id: str, lifecycle: str, response: dict[str, Any]) -> None:
    analysis = response.get("analysis") or {}
    provenance = response.get("provenance") or {}
    print("\n" + "=" * 76, flush=True)
    print(f"CASE: {case_id} | {lifecycle.replace('_', ' ').upper()}", flush=True)
    print("=" * 76, flush=True)
    print(f"OUTCOME: {analysis.get('headline')}", flush=True)
    print(f"SUMMARY: {analysis.get('executive_summary')}", flush=True)
    print(
        f"WHAT BROKE: {(analysis.get('first_failure') or {}).get('text')}",
        flush=True,
    )
    print(
        f"LIKELY CAUSE: {(analysis.get('probable_cause') or {}).get('text')}",
        flush=True,
    )
    print(f"MONEY SAFETY: {(analysis.get('funds_state') or {}).get('text')}", flush=True)
    print(
        f"NEXT / RECOVERY: {(analysis.get('recovery_assessment') or {}).get('text')}",
        flush=True,
    )
    print("ENGINEER CHECKS:", flush=True)
    for index, check in enumerate(analysis.get("recommended_checks") or [], start=1):
        print(f"  {index}. {check}", flush=True)
    print(
        f"AI PROVENANCE: {response.get('status')} | "
        f"{provenance.get('provider')} / {provenance.get('model')}",
        flush=True,
    )
    if provenance.get("fallback_reason"):
        print(f"FALLBACK REASON: {provenance.get('fallback_reason')}", flush=True)


def failed_result(case_id: str, lifecycle: str, message: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "lifecycle": lifecycle,
        "response": {},
        "evaluation": {
            "lifecycle": lifecycle,
            "passed": False,
            "score": 0.0,
            "checks": {},
            "failed_checks": [message],
            "event_count": 0,
            "cited_event_count": 0,
            "first_failed_event": None,
            "headline": None,
            "first_failure_text": None,
            "funds_state_text": None,
            "recovery_text": None,
            "recommended_checks": [],
        },
    }


def normal_case() -> dict[str, Any]:
    run_id = f"phase16g-normal-{uuid4().hex[:12]}"
    destination = f"FW-AI-NORMAL-{uuid4().hex[:10].upper()}"
    key = f"phase16g-normal-{uuid4().hex[:12]}"
    source = os.getenv("DEMO_ACCOUNT_NUMBER", "FW-DEMO-001")
    client = client_for_run(run_id, f"{run_id}-request")
    try:
        checked(client.post("/api/v1/accounts", json={"account_number": destination}))
        transfer = checked(
            client.post(
                "/api/v1/transfers",
                json={
                    "source_account_number": source,
                    "destination_account_number": destination,
                    "amount_minor": 100,
                    "currency": "INR",
                    "idempotency_key": key,
                },
            )
        )
        if transfer.get("state") != "COMPLETED":
            raise RuntimeError(f"normal acceptance transfer is {transfer.get('state')}")
        transfer_id = str(transfer["id"])
        evidence = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence"))
        response = explain(client, transfer_id)
        evaluation = evaluate_assistant_quality(
            response,
            evidence,
            lifecycle="normal",
            forbidden_values=(source, destination, key),
        )
        print_preview("normal-transfer", "normal", response)
        return {
            "case_id": "normal-transfer",
            "lifecycle": "normal",
            "transfer_id": transfer_id,
            "response": response,
            "evaluation": evaluation,
        }
    finally:
        client.close()


def fault_case(
    case_id: str,
    *,
    include_recovery: bool,
    provider_delay_seconds: float = 0.0,
) -> list[dict[str, Any]]:
    definition = TRANSFER_FAULT_CASES[case_id]
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    results: list[dict[str, Any]] = []
    client: httpx.Client | None = None
    try:
        manifest_path = start_case(case_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        observable_path = ROOT / manifest["observable_case"]["path"]
        observable = json.loads(observable_path.read_text(encoding="utf-8"))
        transfer = observable["transfer"]
        client = client_for_run(manifest["run_id"], f"{manifest['run_id']}-assistant-before")
        transfer_id = str(manifest["transfer_id"])
        evidence = checked(client.get(f"/api/v1/transfers/{transfer_id}/evidence"))
        response = explain(client, transfer_id)
        evaluation = evaluate_assistant_quality(
            response,
            evidence,
            lifecycle="awaiting_review",
            expected_failure_step=definition.expected_step,
            forbidden_values=(
                transfer["source_account_number"],
                transfer["destination_account_number"],
                transfer["idempotency_key"],
            ),
        )
        print_preview(case_id, "awaiting_review", response)
        results.append(
            {
                "case_id": case_id,
                "lifecycle": "awaiting_review",
                "transfer_id": transfer_id,
                "response": response,
                "evaluation": evaluation,
            }
        )
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        results.append(failed_result(case_id, "awaiting_review", str(exc)))
    finally:
        if client is not None:
            client.close()

    if manifest is None:
        return results

    try:
        resume_case(
            manifest["run_id"],
            "Phase 16G engineer reviewed cited evidence and restored the dependency",
        )
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        results.append(failed_result(case_id, "recovery_operation", str(exc)))
        return results

    if not include_recovery:
        return results

    if provider_delay_seconds:
        print(
            f"Waiting {provider_delay_seconds:g}s for the Groq token window before "
            "the recovered-case explanation...",
            flush=True,
        )
        time.sleep(provider_delay_seconds)

    recovery_client = client_for_run(manifest["run_id"], f"{manifest['run_id']}-assistant-after")
    try:
        transfer_id = str(manifest["transfer_id"])
        evidence = checked(recovery_client.get(f"/api/v1/transfers/{transfer_id}/evidence"))
        response = explain(recovery_client, transfer_id)
        observable_path = ROOT / manifest["observable_case"]["path"]
        observable = json.loads(observable_path.read_text(encoding="utf-8"))
        transfer = observable["transfer"]
        evaluation = evaluate_assistant_quality(
            response,
            evidence,
            lifecycle="recovered",
            expected_failure_step=definition.expected_step,
            forbidden_values=(
                transfer["source_account_number"],
                transfer["destination_account_number"],
                transfer["idempotency_key"],
            ),
        )
        print_preview(case_id, "recovered", response)
        results.append(
            {
                "case_id": case_id,
                "lifecycle": "recovered",
                "transfer_id": transfer_id,
                "response": response,
                "evaluation": evaluation,
            }
        )
    except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        results.append(failed_result(case_id, "recovered", str(exc)))
    finally:
        recovery_client.close()
    return results


def write_report(
    cases: list[dict[str, Any]],
    *,
    missing: list[str] | None = None,
    report_prefix: str = "acceptance",
) -> tuple[bool, Path, Path]:
    expected_count = len(expected_case_keys())
    missing = missing or []
    passed = (
        not missing
        and len(cases) == expected_count
        and all(item["evaluation"]["passed"] for item in cases)
    )
    report = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "passed": passed,
        "expected_case_count": expected_count,
        "actual_case_count": len(cases),
        "missing_cases": missing,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "protected_fault_truth_sent_to_provider": False,
        "cases": cases,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = OUTPUT_DIRECTORY / f"{report_prefix}-{stamp}.json"
    markdown_path = OUTPUT_DIRECTORY / f"{report_prefix}-{stamp}.md"
    write_json(json_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_quality_report(report), encoding="utf-8", newline="\n")

    print("\n=== PHASE 16G AI QUALITY SUMMARY ===")
    for item in cases:
        evaluation = item["evaluation"]
        result = "PASS" if evaluation["passed"] else "FAIL"
        print(f"{result}: {item['case_id']} [{item['lifecycle']}] score={evaluation['score']:.2%}")
        if evaluation["failed_checks"]:
            print("  Failed: " + ", ".join(evaluation["failed_checks"]))
            fallback_reason = (item.get("response", {}).get("provenance") or {}).get(
                "fallback_reason"
            )
            if fallback_reason:
                print("  Fallback: " + str(fallback_reason))
    if missing:
        print("MISSING CHECKPOINTS:")
        for value in missing:
            print(f"  - {value}")
    print(f"JSON report: {json_path}")
    print(f"Readable report: {markdown_path}")
    print(f"JSON SHA256: {sha256(json_path)}")
    return passed, json_path, markdown_path


def run_one_case(case_id: str) -> int:
    load_dotenv(ROOT / ".env")
    delay = max(
        0.0,
        float(os.getenv("FAULTWEAVE_PHASE16G_PROVIDER_DELAY_SECONDS", "75")),
    )
    if case_id == NORMAL_CASE_ID:
        try:
            results = [normal_case()]
        except (KeyError, OSError, RuntimeError, ValueError, httpx.HTTPError) as exc:
            results = [failed_result(NORMAL_CASE_ID, "normal", str(exc))]
    else:
        results = fault_case(
            case_id,
            include_recovery=case_id == RECOVERY_EXPLANATION_CASE,
            provider_delay_seconds=delay,
        )
    save_checkpoints(results)
    case_passed = all(result["evaluation"]["passed"] for result in results)
    print("\n=== PHASE 16G SINGLE-CASE RESULT ===")
    for result in results:
        evaluation = result["evaluation"]
        status = "PASS" if evaluation["passed"] else "FAIL"
        print(
            f"{status}: {result['case_id']} [{result['lifecycle']}] score={evaluation['score']:.2%}"
        )
        if evaluation["failed_checks"]:
            print("  Failed: " + ", ".join(evaluation["failed_checks"]))
    if case_passed:
        print("PASS: checkpoint saved; this case will not need another provider call")
        return 0
    print("FAIL: checkpoint saved; retry only this case after the provider window resets")
    return 1


def aggregate_checkpoints() -> int:
    cases, missing = load_checkpoints()
    passed, _, _ = write_report(cases, missing=missing)
    if not passed:
        print("FAIL: Phase 16G checkpoint aggregation is incomplete or contains failures")
        return 1
    print(f"PASS: {len(cases)} saved real Groq explanations passed the quality gate")
    print("No Groq request was made during aggregation")
    print("Protected fault labels remained outside provider input")
    return 0


def import_previous_report(path: Path) -> int:
    report = json.loads(path.read_text(encoding="utf-8"))
    values = report.get("cases") if isinstance(report, dict) else None
    if not isinstance(values, list):
        raise RuntimeError(f"Phase 16G report has no case list: {path}")
    expected = set(expected_case_keys())
    imported: list[dict[str, Any]] = []
    skipped: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        identity = (str(value.get("case_id")), str(value.get("lifecycle")))
        response = value.get("response") or {}
        evaluation = value.get("evaluation") or {}
        provenance = response.get("provenance") or {}
        valid = (
            identity in expected
            and evaluation.get("passed") is True
            and response.get("status") == "AI_GENERATED"
            and provenance.get("provider") == "groq"
            and provenance.get("model") == "openai/gpt-oss-120b"
        )
        if valid:
            imported.append(value)
        else:
            skipped.append(f"{identity[0]} [{identity[1]}]")
    if imported:
        save_checkpoints(imported)
    print("\n=== PHASE 16G REPORT IMPORT ===")
    print(f"Imported passing real-AI checkpoints: {len(imported)}")
    if skipped:
        print("Cases still requiring an individual retry:")
        for value in skipped:
            print(f"  - {value}")
    if not imported:
        print("FAIL: no passing real-Groq case was available to import")
        return 1
    print("PASS: existing successful provider results were preserved")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Phase 16G one case at a time and aggregate saved checkpoints."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--case",
        choices=(NORMAL_CASE_ID, *TRANSFER_FAULT_CASES.keys()),
        help="Run exactly one case and save its result.",
    )
    group.add_argument(
        "--aggregate",
        action="store_true",
        help="Validate saved checkpoints without calling Groq.",
    )
    group.add_argument(
        "--import-report",
        type=Path,
        help="Import only passing real-AI cases from an existing acceptance JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.import_report is not None:
        return import_previous_report(args.import_report.resolve())
    if args.aggregate:
        return aggregate_checkpoints()
    return run_one_case(str(args.case))


if __name__ == "__main__":
    raise SystemExit(main())

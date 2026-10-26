# FaultWeave Phase 19B.1 exact-inference-vector capture

Create-only, opt-in diagnostic for the existing frozen 60-second v1 model.

- Disabled by default.
- Never changes the model's vector, decision, threshold, dataset, recovery or funds state.
- Never copies scenario IDs, hidden labels, transfer identifiers or API credentials.
- Creates exclusively new files under the gateway's existing `/faultweave-demo` mount.
- Host path: `data/demo-orchestration/phase19b1-observable/`.
- This cannot reconstruct the October 11 Phase 19B result if that vector was not retained.

## Before changing anything

Run `git status -sb`, `git log -6 --oneline --decorate`, `git diff --check` and keep copies of their output. Ensure `services/gateway/app/transfer_intelligence.py` has no pending edits. Run `git apply --check <patch>` before `git apply <patch>`.

## Tests

`python -m pytest -q tests/test_phase19b1_capture.py tests/test_phase19b1_audit.py`

## Enable only for a new *controlled diagnostic* (not an accepted sealed challenge)

Confirm there is no existing `.env` entry for `FAULTWEAVE_PHASE19B1_CAPTURE` then add `FAULTWEAVE_PHASE19B1_CAPTURE=1` to the ignored `.env`. Verify the gateway's `/faultweave-demo` volume mounts `./data/demo-orchestration`. Recreate only the gateway when a controlled diagnostic is explicitly intended; do not start a challenge automatically.

After a real inference, inspect the newest `observable-*.json` file inside `data/demo-orchestration/phase19b1-observable/`. It contains `snapshot.features`, ordered by the frozen metadata, plus the model's observed candidate and support score. Only run the audit with the matching recorded candidate, score, and support limit (not the old 4916.67 defaults).

Run the existing auditor, substituting actual values from the capture:

`python scripts/audit_phase19b1.py --snapshot <capture.json> --model-root data/datasets/final/models --training-rows data/datasets/final/features-rich-v2/windows-60s.jsonl --candidate <observed_candidate> --expected-score <observed_score> --expected-limit <observed_limit> --report <new_unique_report.json>`

Audit cannot prove phase isolation or client/event source completeness from aggregate features alone. Capture those provenance fields separately in Phase 19B.2 without reading hidden fault labels.

Disable capture afterward by setting the ignored `.env` entry to `0` and recreating the gateway at the next safe maintenance point. Never commit `.env` or diagnostic reports.

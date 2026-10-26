# FaultWeave Model v2 — First independently admitted fresh captures

## What this adds

- New research capture: `scripts/faultweave_v2_capture.py`.
- Independent audit and create-only admission: `scripts/faultweave_v2_independent_audit.py`.
- Offline safety/evidence tests: `tests/test_faultweave_v2_capture.py`.

This is **an executable first slice, not all nine classes or 126 runs yet**. It supports
`NORMAL` and the independently proven `DOWNSTREAM_ERROR_BURST` at **payment**, medium
intensity, 1.5 RPS. These executions create genuinely new client requests using
`X-Run-ID`; unlike historical logs or diagnostic runs, the intended label, split,
slot, and run purpose are persisted **before** any traffic.

Both captures use 75-second workloads and must pass the strict 60-second window,
65-feature schema, run/trace correlation and fail-closed independent audit. A
fault run additionally requires payment error impact and 8 successful post-clear
requests. The fault lease is released in the existing runner's `finally` block.
The capture script never stops PostgreSQL or changes Model v1/frontend.

## Safe installation and one-run verification

With the existing venv active in project root, run the guarded installer `--check`
then `--apply`. Run:

```powershell
python -m pytest -q tests/test_faultweave_v2_capture.py
python -m scripts.faultweave_v2_capture --preview --label NORMAL --slot 1
```

When ready to generate one real NEW healthy training sample (writes normal test
transactions to the local development database, no database stopping):

```powershell
python -m scripts.faultweave_v2_capture --capture --label NORMAL --slot 1 --acknowledge-fresh-research-traffic
python -m scripts.faultweave_v2_independent_audit --check --label NORMAL --slot 1
python -m scripts.faultweave_v2_independent_audit --admit --label NORMAL --slot 1
```

The first two commands after capture are an independent read-only audit and a
create-only admission record. **Only run `--admit` if `--check` passes.** Never
re-run the same capture slot; an incomplete slot and active reservation must
be inspected before any new run. Don't manually delete the reservation or
change training labels/hashes.

To collect the first verified known-fault training sample after the normal
capture is admitted:

```powershell
python -m scripts.faultweave_v2_capture --preview --label DOWNSTREAM_ERROR_BURST --slot 1
python -m scripts.faultweave_v2_capture --capture --label DOWNSTREAM_ERROR_BURST --slot 1 --acknowledge-fresh-research-traffic
python -m scripts.faultweave_v2_independent_audit --check --label DOWNSTREAM_ERROR_BURST --slot 1
python -m scripts.faultweave_v2_independent_audit --admit --label DOWNSTREAM_ERROR_BURST --slot 1
```

## What is deliberately NOT implemented here

- Other seven known classes; no false claims of nine-class readiness.
- Automatic multi-run execution or unreviewed admission.
- A completed 126-row dataset or a trained/active Model v2.
- Database-unavailable on the existing persistent PostgreSQL volume.
- High-load overload labels from the 8 RPS result (not proven).
- Authentication-burst labels from low-rate invalid-login rejections.
- Promotion of historical or diagnostic samples.

The existing FastTrack 126-row training gate remains blocked until **all nine
classes** are collected and independently admitted. More adapters must use this
new-capture-and-independent-audit contract; merely copying diagnostic artifacts
or renaming their split fields is not acceptable. These first 2 classes are
proof-of-workflow. For model-quality generalization, later runs need truly varied
verified configurations, not blind repetitions of one 1.5 RPS setting.

## Important scope

All requests are local synthetic banking API transactions and **may write records
to the persistent development database**. They do not use personal banking
credentials, stop the database, or touch a production service. Test only on your
local development stack.

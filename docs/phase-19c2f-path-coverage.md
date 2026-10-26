# FaultWeave — Phase 19C.2F: research workload path coverage

**Status:** read-only diagnostic. Does not approve Model v2 training, rates, or bulk collection.

## Why this gate comes before rate calibration

The blocked 19C.2E matrix rotates targets for all nine classes. Some technically valid fault targets may not lie on the research transaction API path. Earlier `payment->account` blind timeout produced an invalid test because that edge was transfer-specific. The draft must not schedule those combinations as successful training fault windows without proving actual exposure.

## Evidence and procedure

Audit the exact `phase19c2e-draft-matrix.json` checksum and blocked flags. Verify artifact hashes and correlation IDs from the independently verified 2-RPS healthy research pilot (`v2pilot-01f0974b69ce4220`). Derive distinct client request counts for each observed `downstream_request_completed` or `downstream_request_failed` edge and each emitting service. Compare all 240 planned rows against the *actual* observed path. An edge is provisionally reachable only with at least 10 distinct request IDs in the sample. An absent/weak target is **NO-GO**. High load, auth burst, database faults and infrastructure outages require separate mechanism-specific approval even if their services are present.

```powershell
python scripts/phase19c2f_path_audit.py --dry-run
python -m pytest -q tests/test_phase19c2f_path_audit.py
python scripts/phase19c2f_path_audit.py --audit
Write-Host "Audit exit code: $LASTEXITCODE"
```

Exit code `2` is an intentional no-go if unobservable/insufficient target rows are discovered. Code `3` indicates invalid evidence. A code `0` means **reachability only**; it never approves any run.

## Scientific and operational limits

- Healthy logs prove dependency *reachability* only—not injected fault response, database involvement, correct target on every request, recovery or safe RPS.
- An edge absent in one healthy pilot is not proof that it can never occur. Investigate it via code and separate pilot, but never approve the draft based on assumptions.
- No training/validation/test rows are re-split, no draft content is mutated, and no unknown holdout is reused.
- Do not execute bulk collection, restart Docker, enable fault injection, modify Model v1, or change v1 calibration.

**Next gates:** explicitly annotate eligible target coverage without modifying draft; calibrate gentle *healthy-only* rates (2 RPS is the sole validated reference) under bounded concurrency and stop criteria; then class-by-class exposure/recovery pilots, new locked plan, independent test/unknown evaluation.

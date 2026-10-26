# FaultWeave Model v2: bounded authentication-burst capture and feature assembly

## Scope

This adds the *first fresh research collection path* for `AUTHENTICATION_FAILURE_BURST` and an independent auditor. It uses a bounded 5 RPS stream of invalid-login requests against only the localhost FaultWeave gateway for 75 seconds. A valid 60-second feature window must exhibit >=4.5 observed requests/second, >=90% client HTTP 401 responses, and >=75% trace-matched authentication HTTP 401 events. Eight valid normal recovery requests and full 65-feature parity remain mandatory. This is a **relative burst (~3x the established 1.5 RPS rate)**. It **does not** claim that the originally proposed 20-60 RPS absolute burst regime has been verified. Keep those higher-rate claims blocked.

## Commands

From the FaultWeave repo root, after reviewing all seven containers' Docker health and supervisor lock status:

```powershell
python -m pytest -q tests/test_faultweave_v2_auth_burst.py
python -m scripts.faultweave_v2_auth_burst --preview --start-slot 1 --count 1
python -m scripts.faultweave_v2_auth_burst --batch --start-slot 1 --count 1 --acknowledge-bounded-auth-burst
```

The live command writes new `training-captures/authentication_failure_burst-01` in create-only mode. No slots may be retried or overwritten silently. The independent auditor runs as a separate process and rejects unproven burst evidence. If the first slot fails: preserve journal/lock and investigate rather than rerun. If it passes: subsequent `--start-slot 2 --count 7` covers the remaining first training allocation.

The other unsafe/unverified families `DATABASE_UNAVAILABLE` and `HIGH_LOAD` are intentionally **not** enabled by this package. They need an isolated database and demonstrated overload respectively.

## Assembly

Use the included `scripts.faultweave_v2_assembly` for an overview of the **already independently admitted** samples:

```powershell
python -m scripts.faultweave_v2_assembly --status
```

This command is read-only and can run before all 126 are ready. Only after 126 correctly split and independently admitted captures exist, run:

```powershell
python -m scripts.faultweave_v2_assembly --assemble
```

The assembler independently verifies captured artifacts, manifests, receipts, and raw hashes. It writes `data/datasets/v2/assembled/verified-126-v1/features.jsonl` and `admission_book.json` **create-only**. It explicitly restores the frozen 65-feature column order, preventing the previous JSON key-order problem from recurring in FastTrack.

The assembler does not activate Model v2 or change Model v1. Once assembly succeeds, train **only a candidate**, and review its actual evaluation before activating it in the API/frontend.

## Limitations

Offline tests mock the banking/network interfaces. Live capability is not confirmed until slot 1 passes the independent audit on the user's stack. One exact-window sample per run is small; validation/test at the same fixed conditions measures within-configuration generalization, not every possible real-world workload. Higher-rate auth, isolated database outage, proven load-induced degradation, sealed unknown evaluation, and frontend integration remain separate tasks.

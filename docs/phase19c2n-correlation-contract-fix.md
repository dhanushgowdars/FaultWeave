# Resource fault correlation field mismatch — targeted correction

The **resource collector** used `event.get('outcome') == 'failure'`, but the shared healthy-v2 projection (`visible_events`) exports a privacy-limited schema with `success`, `status_code`, and `event_type`, **not** `outcome`. Consequently, the resource collector systematically counted zero correlated failures when given correctly exported logs.

This change affects only the resource-effect gate and its offline tests. It requires `success is False` plus an HTTP 5xx completion or a narrowly allowlisted failure event type. The original request ID, trace ID, service, window, client-failure, physical-DB-pressure, advisory-lock, and recovery thresholds remain mandatory. Other collectors and 65-feature extraction are unchanged.

`phase19c2n_rejected_replay.py` can inspect the immutable saved `v2resource-e45a8dd275a84466` rejected diagnostic using the corrected schema, without changing its rejection verdict, touching a lock, or admitting it into training. Run it via `python -m scripts.phase19c2n_rejected_replay` after installing and testing.

**Important:** The failed `resource-pool-payment-001` supervisor reservation remains locked; do not clear it manually or launch a new fault. A separate owner-verified, health-gated reconciliation is needed. The historical attempt stays rejected, even if offline replay now finds correlated failures.

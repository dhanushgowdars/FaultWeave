"""Offline acceptance safety checks; never injects a fault or sends traffic."""
from __future__ import annotations

import asyncio
import tempfile
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from scripts import faultweave_v2_isolated_fault_acceptance as acceptance


class FaultAcceptanceTests(unittest.TestCase):
    def test_mutating_run_requires_explicit_fault_and_traffic_ack(self):
        with patch.object(acceptance, "run", side_effect=AssertionError("fault started")):
            with self.assertRaisesRegex(RuntimeError, "explicit isolated fault"):
                acceptance.main(["--run"])

    def test_effect_and_exact_frozen_window_must_both_pass(self):
        activated = datetime.now(UTC)
        cleared = activated + timedelta(seconds=80)
        rows = [{"request_id": f"request-{i}", "status_code": 503 if i < 20 else 200,
                 "transport_error": None} for i in range(113)]
        receipt = {"window_started_at": (activated+timedelta(seconds=3)).isoformat(),
                   "window_ended_at": (activated+timedelta(seconds=63)).isoformat(),
                   "request_count": 90, "event_count": 1900,
                   "prediction": "SERVICE_UNAVAILABLE", "receipt_sha256": "a"*64}
        self.assertEqual(acceptance.evaluate(rows, [receipt], activated=activated, cleared=cleared)["status"], "PASS")
        self.assertEqual(acceptance.evaluate(rows, [{**receipt, "prediction": "NORMAL"}],
                                             activated=activated, cleared=cleared)["status"], "MISMATCH")
        with self.assertRaisesRegex(RuntimeError, "exact timezone-aware 60 seconds"):
            acceptance.evaluate(rows, [{**receipt, "window_ended_at":
                           (activated+timedelta(seconds=64)).isoformat()}],
                           activated=activated, cleared=cleared)
        self.assertEqual(acceptance.evaluate([{**r, "status_code": 200} for r in rows],
                           [receipt], activated=activated, cleared=cleared)["status"], "MISMATCH")
        with self.assertRaisesRegex(RuntimeError, "identity incomplete"):
            acceptance.evaluate(rows[:-1], [receipt], activated=activated, cleared=cleared)

    def test_fault_lease_is_released_when_client_runner_fails(self):
        from experiments.faults import lifecycle
        from scripts import faultweave_v2_live_observer as observer
        from scripts import faultweave_v2_candidate04_shadow_watch as watcher
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activation = MagicMock()
            activation.activation_id = "a"*32
            activation.activated_at = datetime.now(UTC)
            lease = MagicMock()
            lease.acquire.return_value = activation
            fake_runner = types.ModuleType("experiments.runner")
            fake_runner.PlannedRequest = lambda **kwargs: kwargs
            fake_runner.execute_plan = AsyncMock(side_effect=RuntimeError("runner failed"))
            with (patch.object(acceptance, "preflight", return_value=tuple()),
                  patch.dict(sys.modules, {"experiments.runner": fake_runner}),
                  patch.object(observer, "OUT", root),
                  patch.object(watcher, "OUT", root / "receipts"),
                  patch.object(observer, "collect_once", return_value="WAIT"),
                  patch.object(lifecycle, "FaultStateStore", return_value=lease)):
                with self.assertRaisesRegex(RuntimeError, "runner failed"):
                    asyncio.run(acceptance.run())
                lease.release.assert_called_once_with(activation.activation_id)
                self.assertFalse((root / "active.lock").exists())


if __name__ == "__main__":
    unittest.main()

"""Offline fail-closed checks; this file never generates transactions."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import faultweave_v2_live_observer_pilot as pilot
from scripts import faultweave_v2_candidate04_shadow_watch as watcher


class PilotTests(unittest.TestCase):
    def test_run_requires_explicit_transaction_ack(self) -> None:
        with patch.object(pilot, "run", side_effect=AssertionError("traffic began")):
            with self.assertRaisesRegex(RuntimeError, "explicit opt-in"):
                pilot.main(["--run"])

    def test_status_fails_closed_when_observer_identity_changes(self) -> None:
        with patch("scripts.faultweave_v2_live_observer_pilot.urlopen") as open_url:
            response = open_url.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b'{"status":"READY","run_id":"wrong","generates_traffic":false,"fault_control_enabled":false}'
            # json.load uses read, so a substituted service must be rejected.
            with self.assertRaises(RuntimeError):
                pilot.observer_status()

    def test_only_new_frozen_research_receipts_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime.now(UTC)
            path = root / "a.prediction.json"
            payload = {"created_at": now.isoformat(), "window_ended_at": now.isoformat(),
                       "status": "RESEARCH_ONLY_SHADOW_MONITOR", "model_sha256": watcher.MODEL_SHA256,
                       "training_eligible": False, "window_seconds": 60, "prediction": "NORMAL"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(watcher, "OUT", root):
                result = pilot.new_receipts(set(), now - timedelta(seconds=1))
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["prediction"], "NORMAL")
                self.assertEqual(pilot.new_receipts({path.name}, now - timedelta(seconds=1)), [])
                payload["model_sha256"] = "0" * 64
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "provenance"):
                    pilot.new_receipts(set(), now - timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()

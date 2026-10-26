"""Saved lab reports must be tied to the actual frozen prediction receipt."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.faultweave_candidate04_acceptance_readout import acceptance_matrix, saved_acceptance_runs
from scripts import faultweave_v2_candidate04_shadow_watch as watch


class AcceptanceReadoutTests(unittest.TestCase):
    def test_acceptance_requires_matching_receipt_and_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); reports = root / 'reports'; receipts = root / 'receipts'
            reports.mkdir(); receipts.mkdir()
            start = '2026-10-26T12:00:00+00:00'; end = '2026-10-26T12:01:00+00:00'
            receipt = {'status': 'RESEARCH_ONLY_SHADOW_MONITOR',
                       'deployment_status': 'RESEARCH_ONLY_NOT_DEPLOYED',
                       'training_eligible': False, 'model_sha256': watch.MODEL_SHA256,
                       'prediction': 'SERVICE_UNAVAILABLE', 'window_started_at': start,
                       'window_ended_at': end, 'request_count': 90, 'event_count': 2250}
            path = receipts / 'saved.prediction.json'
            path.write_text(json.dumps(receipt), encoding='utf-8')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            run_id = 'v2observer-' + 'a'*16
            report = {'run_id': run_id, 'status': 'PASS', 'expected_family': 'SERVICE_UNAVAILABLE',
                      'fault_target': 'payment', 'frozen_model_sha256': watch.MODEL_SHA256,
                      'training_eligible': False, 'threshold_tuning_eligible': False,
                      'ground_truth_used_for_inference': False,
                      'original_gateway_written': False, 'fault_cleared': True,
                      'total_client_requests': 113, 'recovery_completed_requests': 8,
                      'observed_failed_client_requests': 90, 'observed_effect': True,
                      'failed_service_event_count': 200, 'service_failure_evidence_observed': True,
                      'family_predicted': True, 'created_at': end,
                      'first_failure_evidence': [{'timestamp': start, 'service': 'payment',
                                                  'event_sha256': 'b'*64}],
                      'eligible_frozen_receipts': [{**{key: receipt[key] for key in
                          ('prediction', 'window_started_at', 'window_ended_at', 'request_count',
                           'event_count')}, 'receipt_sha256': digest}]}
            saved = reports / (run_id + '.result.json')
            saved.write_text(json.dumps(report), encoding='utf-8')
            rows = saved_acceptance_runs(reports, receipts)
            self.assertEqual(rows[0]['model_predictions'], ['SERVICE_UNAVAILABLE'])
            self.assertFalse(rows[0]['root_cause_confirmed'])
            self.assertEqual(rows[0]['expected_family'], 'SERVICE_UNAVAILABLE')
            matrix = acceptance_matrix(rows)
            self.assertEqual(len(matrix), 9)
            service = next(row for row in matrix if row['family'] == 'SERVICE_UNAVAILABLE')
            self.assertEqual(service['status'], 'PASS')
            self.assertEqual(service['run_id'], run_id)
            self.assertEqual(sum(row['status'] == 'NOT_RUN' for row in matrix), 8)
            receipt['prediction'] = 'NORMAL'
            path.write_text(json.dumps(receipt), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match frozen evidence'):
                saved_acceptance_runs(reports, receipts)
            path.write_text(json.dumps({**receipt, 'prediction': 'SERVICE_UNAVAILABLE'}), encoding='utf-8')
            report['status'] = 'MISMATCH'
            saved.write_text(json.dumps(report), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'verdict conflicts'):
                saved_acceptance_runs(reports, receipts)


if __name__ == '__main__':
    unittest.main()

"""Offline contract tests; NEVER starts traffic, Docker, or faults."""
from pathlib import Path
import json
import importlib.util
import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts' / 'faultweave_v2_isolated_unknown_capture.py'
if not SOURCE.is_file():
    SOURCE = Path(__file__).parent / 'faultweave_v2_isolated_unknown_capture.py'
SPEC = importlib.util.spec_from_file_location('fw_isolated_eval_capture', SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_preview_is_read_only(capsys):
    assert module.main(['--preview']) == 0
    out = capsys.readouterr().out
    assert '127.0.0.1:28110' in out and '60s feature window' in out


def test_capture_requires_explicit_acknowledgement():
    with pytest.raises(RuntimeError, match='explicit acknowledgement'):
        module.main(['--capture', '--scenario', 'LATENCY_JITTER_PARTIAL_DEGRADATION', '--target', 'payment', '--intensity', 'medium'])


def test_preview_rejects_execution_ack():
    with pytest.raises(RuntimeError, match='preview must not'):
        module.main(['--preview', '--acknowledge-isolated-sealed-unknown-traffic'])


def test_create_only_json_preserves_original(tmp_path):
    path = tmp_path / 'capture.json'
    module.write_new(path, {'run_id':'v2blind-test', 'training_eligible':False})
    with pytest.raises(RuntimeError, match='refuse to overwrite'):
        module.write_new(path, {'run_id':'SHOULD_NOT_OVERWRITE'})
    assert json.loads(path.read_text())['run_id'] == 'v2blind-test'


def test_create_only_jsonl_preserves_original(tmp_path):
    path = tmp_path / 'events.jsonl'
    module.jsonl_new(path, [{'run_id':'x','service':'gateway'}])
    with pytest.raises(RuntimeError, match='refuse to overwrite'):
        module.jsonl_new(path, [{'run_id':'y'}])
    assert json.loads(path.read_text())['run_id'] == 'x'


def test_roots_are_evaluation_only():
    assert module.GATEWAY == 'http://127.0.0.1:28110'
    assert 'evaluation-only' in str(module.ATTEMPTS)
    assert 'v2-sealed-evaluation-only' in str(module.RAW)
    assert 'v2-sealed-evaluation-only' in str(module.TRUTH)
    assert module.LEASE_SECONDS > module.FAULT_PHASE_SECONDS

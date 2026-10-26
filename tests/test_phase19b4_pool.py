"""Phase 19B.4 model-ready pool / legacy commitment regression tests."""
from __future__ import annotations

import hashlib

import pytest
from app import demo_orchestration as d
from app.phase19b3_observability import SCENARIOS, _unobservable


def test_model_ready_pool_is_exact_research_compatible_subset():
    assert set(d.MODEL_READY_BLIND_SCENARIO_IDS) == {
        scenario for scenario in d.BLIND_SCENARIO_IDS if not _unobservable(scenario)
    }
    assert set(d.MODEL_READY_BLIND_SCENARIO_IDS).issubset(SCENARIOS)
    assert 'account-response-timeout' not in d.MODEL_READY_BLIND_SCENARIO_IDS
    assert 'account-response-timeout' in d.BLIND_SCENARIO_IDS
    assert len(d.MODEL_READY_BLIND_SCENARIO_IDS) == 5


def test_old_blind_selection_and_commitment_unchanged(monkeypatch):
    monkeypatch.delenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', raising=False)
    nonce = 'legacy-selector-proof'
    digest = hashlib.sha256(f'{d.BLIND_CHALLENGE_VERSION}:{nonce}'.encode()).digest()
    expected = d.BLIND_SCENARIO_IDS[int.from_bytes(digest[:8], 'big') % len(d.BLIND_SCENARIO_IDS)]
    assert d._blind_scenario_id(nonce) == expected
    assert d._blind_scenario_id_for_version(nonce, d.BLIND_CHALLENGE_VERSION) == expected
    legacy = d.new_session(d.DemoSessionCreate(mode='BLIND_RANDOM'), 'test-engineer')
    assert legacy['challenge']['selector_version'] == d.BLIND_CHALLENGE_VERSION
    assert legacy['challenge']['pool_size'] == len(d.BLIND_SCENARIO_IDS)
    assert d.verify_blind_commitment(legacy['challenge'], legacy['scenario']['scenario_id'])


def test_opt_in_uses_five_way_versioned_blind_selection(monkeypatch):
    monkeypatch.setenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', '1')
    for _ in range(25):
        session = d.new_session(d.DemoSessionCreate(mode='BLIND_RANDOM'), 'test-engineer')
        assert session['challenge']['selector_version'] == d.MODEL_READY_SELECTOR_VERSION
        assert session['challenge']['pool_size'] == 5
        assert session['scenario']['scenario_id'] in d.MODEL_READY_BLIND_SCENARIO_IDS
        assert d.verify_blind_commitment(session['challenge'], session['scenario']['scenario_id'])
        public = d.public_session(session)
        assert public['scenario']['scenario_id'] == 'SEALED_RANDOM_CHALLENGE'
        assert 'secret_nonce' not in public['challenge']


def test_pool_reaches_every_supported_scenario_without_label_leakage():
    seen = {d._blind_scenario_id_for_version(f'proof-{i}', d.MODEL_READY_SELECTOR_VERSION)
            for i in range(500)}
    assert seen == set(d.MODEL_READY_BLIND_SCENARIO_IDS)


def test_modifying_selector_version_breaks_commitment(monkeypatch):
    monkeypatch.setenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', '1')
    session = d.new_session(d.DemoSessionCreate(mode='BLIND_RANDOM'), 'test-engineer')
    original = session['challenge']
    forged = {**original, 'selector_version': d.BLIND_CHALLENGE_VERSION}
    assert not d.verify_blind_commitment(forged, session['scenario']['scenario_id'])


def test_unsupported_version_or_scenario_is_rejected(monkeypatch):
    monkeypatch.setenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', '1')
    session = d.new_session(d.DemoSessionCreate(mode='BLIND_RANDOM'), 'test-engineer')
    bad = {**session['challenge'], 'selector_version': 'unknown-version'}
    assert not d.verify_blind_commitment(bad, session['scenario']['scenario_id'])
    assert not d.verify_blind_commitment(session['challenge'], 'account-response-timeout')
    with pytest.raises(ValueError):
        d._blind_scenario_id_for_version('nonce', 'unknown-version')


def test_old_challenge_stays_compatible_with_new_model_ready_mode(monkeypatch):
    monkeypatch.delenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', raising=False)
    old = d.new_session(d.DemoSessionCreate(mode='BLIND_RANDOM'), 'test-engineer')
    monkeypatch.setenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', '1')
    assert d.verify_blind_commitment(old['challenge'], old['scenario']['scenario_id'])


def test_unsupported_legacy_scenario_remains_available_for_controlled_demo(monkeypatch):
    monkeypatch.setenv('FAULTWEAVE_MODEL_READY_BLIND_POOL', '1')
    controlled = d.new_session(
        d.DemoSessionCreate(mode='CONTROLLED', scenario_id='account-response-timeout'),
        'test-engineer',
    )
    assert controlled['scenario']['scenario_id'] == 'account-response-timeout'
    assert controlled['challenge'] is None

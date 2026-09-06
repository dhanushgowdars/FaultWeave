from scripts.verify_phase4 import main


def test_phase4_contract_verifier(capsys) -> None:
    assert main() == 0
    assert "PASS: Phase 4 fault-safety contract verified" in capsys.readouterr().out

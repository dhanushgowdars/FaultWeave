from scripts.verify_phase4c import main


def test_phase4c_verifier_passes(capsys) -> None:
    assert main() == 0
    assert "PASS: Phase 4C sealed-unknown contract verified" in capsys.readouterr().out

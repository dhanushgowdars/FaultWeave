"""Read-only Phase 19B.3 post-reveal acceptance auditor.

Usage:
  python scripts/phase19b3_audit.py data/experiments/phase19b-acceptance/acceptance-*.json
No model, backend, Docker or training run is invoked.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "gateway"))
from app.phase19b3_observability import assess_acceptance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("acceptance", type=Path, help="one existing acceptance JSON file")
    args = parser.parse_args()
    result = json.loads(args.acceptance.read_text(encoding="utf-8"))
    audit = assess_acceptance(result)
    print(json.dumps({"session_id": result.get("session_id"), **audit}, indent=2))
    return 0 if audit["status"] == "ACCEPTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from uuid import uuid4


def main() -> int:
    gateway_url = os.getenv("FAULTWEAVE_GATEWAY_URL", "http://localhost:18110")
    request_id = f"phase-1-smoke-{uuid4().hex[:12]}"
    payload = json.dumps(
        {
            "username": "demo",
            "password": "faultweave-demo",
            "amount_minor": 12500,
            "currency": "INR",
            "recipient": "merchant-demo",
        }
    ).encode()
    request = urllib.request.Request(
        f"{gateway_url}/api/v1/transactions",
        data=payload,
        headers={"Content-Type": "application/json", "X-Request-ID": request_id},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode(errors="replace")
        print(
            f"FAIL: gateway returned HTTP {exc.code}: {response_body}",
            file=sys.stderr,
        )
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL: gateway request failed: {exc}", file=sys.stderr)
        return 1
    if result.get("status") != "COMPLETED" or result.get("request_id") != request_id:
        print(f"FAIL: unexpected response: {result}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print("PASS: Phase 1 normal transaction flow completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

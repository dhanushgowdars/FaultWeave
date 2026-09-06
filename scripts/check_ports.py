from __future__ import annotations

import socket
import subprocess
from pathlib import Path

REQUIRED_PORTS = {
    "PostgreSQL": 15434,
    "API Gateway": 18110,
    "Authentication": 18111,
    "Transaction": 18112,
    "Payment": 18113,
    "Account": 18114,
    "Ledger": 18115,
}


def faultweave_is_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--services", "--status", "running"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    project_directory = Path(__file__).resolve().parents[1]
    if faultweave_is_running():
        print("INFO: FaultWeave is already running.")
        print("Its host ports are expected to be in use; no preflight conflict exists.")
        subprocess.run(["docker", "compose", "ps"], cwd=project_directory, check=False)
        return 0

    occupied_ports: list[int] = []
    for service, port in REQUIRED_PORTS.items():
        if port_is_available(port):
            print(f"FREE    {service} port {port}")
        else:
            print(f"IN USE  {service} port {port}")
            occupied_ports.append(port)

    if occupied_ports:
        print(
            "FAIL: FaultWeave is stopped, but required ports are occupied by another application."
        )
        return 1

    print("PASS: All default FaultWeave host ports are free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

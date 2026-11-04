"""Wait until a TCP port accepts connections, then exit 0.

    python scripts/wait_for.py postgres:5432 --timeout 300

Used before a step that needs a dependency that may still be starting,
such as the migration Job on a new environment.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="host:port")
    parser.add_argument("--timeout", type=float, default=300, help="seconds (default 300)")
    args = parser.parse_args()

    host, _, port = args.target.rpartition(":")
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            socket.create_connection((host, int(port)), timeout=2).close()
            print(f"{args.target} is up")
            return 0
        except OSError as exc:
            if time.monotonic() >= deadline:
                print(f"{args.target} not reachable after {args.timeout:.0f}s: {exc}", file=sys.stderr)
                return 1
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())

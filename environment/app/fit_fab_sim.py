#!/usr/bin/env python3
"""Partial fab-line calibration entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("REFUSED: expected input and output paths", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    payload = json.loads(source.read_text(encoding="utf-8"))
    parameters = {
        "service_a_sec": 50,
        "service_b_sec": 40,
        "buffer_capacity": 2,
        "rework_percent": 0,
    }
    result = {
        "status": "ok",
        "parameters": parameters,
        "probe": {
            "completed": 0,
            "wip_end": len(payload.get("probe", {}).get("release_min", [])),
            "busy_a_sec": 0,
            "busy_b_sec": 0,
            "blocked_a_sec": 0,
            "blocked_b_sec": 0,
        },
    }
    target.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

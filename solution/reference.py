#!/usr/bin/env python3
"""Reference calibrator for the two-station fab simulation."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import deque
from pathlib import Path
from typing import Any


TOP_KEYS = {"schema", "calibration_runs", "probe"}
RUN_ID = re.compile(r"RUN-[A-Z0-9]{3}\Z")
OBSERVATION_KEYS = {
    "completed",
    "wip_end",
    "busy_a_sec",
    "busy_b_sec",
    "blocked_a_sec",
    "blocked_b_sec",
}
PARAMETER_RANGES = {
    "service_a_sec": (35, 75),
    "service_b_sec": (25, 65),
    "buffer_capacity": (1, 4),
    "rework_percent": (5, 30),
}


class Refused(ValueError):
    pass


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(f"{label} is not an integer")
    return value


def _releases(value: Any, label: str) -> list[int]:
    if not isinstance(value, list) or not 8 <= len(value) <= 18:
        raise Refused(f"{label} must contain 8 to 18 release minutes")
    result = [_integer(item, label) for item in value]
    if result[0] != 0:
        raise Refused(f"{label} must start at zero")
    if any(result[index] > result[index + 1] for index in range(len(result) - 1)):
        raise Refused(f"{label} must be nondecreasing")
    return result


def parse_payload(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused("input JSON is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS:
        raise Refused("top-level keys are invalid")
    if payload.get("schema") != "fab-line-v1":
        raise Refused("schema is unsupported")
    raw_runs = payload.get("calibration_runs")
    if not isinstance(raw_runs, list) or not 5 <= len(raw_runs) <= 8:
        raise Refused("calibration_runs size is outside 5 to 8")
    runs: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    for raw_run in raw_runs:
        if not isinstance(raw_run, dict) or set(raw_run) != {
            "run_id",
            "seed",
            "horizon_min",
            "release_min",
            "observations",
        }:
            raise Refused("calibration run keys are invalid")
        run_id = raw_run.get("run_id")
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise Refused("run id is invalid")
        if run_id in run_ids:
            raise Refused("run ids must be unique")
        run_ids.add(run_id)
        seed = _integer(raw_run.get("seed"), "seed")
        horizon = _integer(raw_run.get("horizon_min"), "horizon_min")
        if not 0 <= seed <= 999 or not 120 <= horizon <= 360:
            raise Refused("seed or horizon is outside its range")
        observations = raw_run.get("observations")
        if not isinstance(observations, dict) or set(observations) != OBSERVATION_KEYS:
            raise Refused("observation keys are invalid")
        parsed_observations = {
            key: _integer(observations.get(key), key) for key in OBSERVATION_KEYS
        }
        if any(value < 0 for value in parsed_observations.values()):
            raise Refused("observation values must be nonnegative")
        runs.append(
            {
                "run_id": run_id,
                "seed": seed,
                "horizon_min": horizon,
                "release_min": _releases(raw_run.get("release_min"), "release_min"),
                "observations": parsed_observations,
            }
        )
    raw_probe = payload.get("probe")
    if not isinstance(raw_probe, dict) or set(raw_probe) != {
        "seed",
        "horizon_min",
        "release_min",
    }:
        raise Refused("probe keys are invalid")
    seed = _integer(raw_probe.get("seed"), "probe seed")
    horizon = _integer(raw_probe.get("horizon_min"), "probe horizon")
    if not 0 <= seed <= 999 or not 120 <= horizon <= 360:
        raise Refused("probe seed or horizon is outside its range")
    probe = {
        "seed": seed,
        "horizon_min": horizon,
        "release_min": _releases(raw_probe.get("release_min"), "probe release_min"),
    }
    return runs, probe


def simulate(
    run: dict[str, Any],
    parameters: dict[str, int],
) -> dict[str, float]:
    total_lots = len(run["release_min"])
    releases = list(run["release_min"])
    release_index = 0
    a_queue: deque[int] = deque()
    b_buffer: deque[tuple[int, int]] = deque()
    a_active: list[int] | None = None
    a_remaining = 0
    a_blocked: list[int] | None = None
    b_active: list[int] | None = None
    b_remaining = 0
    b_blocked: tuple[int, int] | None = None
    completed = 0
    busy_a = 0
    busy_b = 0
    blocked_a = 0
    blocked_b = 0
    capacity = parameters["buffer_capacity"]

    for second in range(run["horizon_min"] * 60):
        minute = second // 60
        while release_index < total_lots and releases[release_index] == minute:
            a_queue.append(release_index)
            release_index += 1

        if a_blocked is not None and len(b_buffer) < capacity:
            b_buffer.append(a_blocked)
            a_blocked = None
        if b_blocked is not None and len(b_buffer) < capacity:
            b_buffer.append(b_blocked)
            b_blocked = None

        if a_active is None and a_blocked is None and a_queue:
            a_active = [a_queue.popleft(), 1]
            a_remaining = parameters["service_a_sec"]
        if b_active is None and b_buffer:
            lot, attempt = b_buffer.popleft()
            b_active = [lot, attempt]
            b_remaining = parameters["service_b_sec"]

        if a_active is not None:
            busy_a += 1
            a_remaining -= 1
            if a_remaining == 0:
                lot = a_active[0]
                a_active = None
                if len(b_buffer) < capacity:
                    b_buffer.append((lot, 1))
                else:
                    a_blocked = (lot, 1)
        elif a_blocked is not None:
            blocked_a += 1

        if b_active is not None:
            busy_b += 1
            b_remaining -= 1
            if b_remaining == 0:
                lot, attempt = b_active
                b_active = None
                rework_roll = (
                    run["seed"] * 31 + lot * 1009 + attempt * 9176
                ) % 100
                if rework_roll < parameters["rework_percent"] and attempt < 3:
                    item = (lot, attempt + 1)
                    if len(b_buffer) < capacity:
                        b_buffer.append(item)
                    else:
                        b_blocked = item
                else:
                    completed += 1
        if b_blocked is not None:
            blocked_b += 1

    return {
        "completed": completed,
        "wip_end": total_lots - completed,
        "busy_a_sec": busy_a,
        "busy_b_sec": busy_b,
        "blocked_a_sec": blocked_a,
        "blocked_b_sec": blocked_b,
    }


def objective(
    runs: list[dict[str, Any]], parameters: dict[str, int]
) -> float:
    error = 0.0
    for run in runs:
        predicted = simulate(run, parameters)
        observed = run["observations"]
        error += 250.0 * (predicted["completed"] - observed["completed"]) ** 2
        error += 180.0 * (predicted["wip_end"] - observed["wip_end"]) ** 2
        for key in ("busy_a_sec", "busy_b_sec", "blocked_a_sec", "blocked_b_sec"):
            error += ((predicted[key] - observed[key]) / 10.0) ** 2
    return error


# 同一组参数必须跨运行解释所有放料，有限缓冲会形成真实阻塞区间。
def calibrate(runs: list[dict[str, Any]]) -> dict[str, int]:
    parameters = {
        "service_a_sec": 55,
        "service_b_sec": 45,
        "buffer_capacity": 2,
        "rework_percent": 15,
    }
    for _ in range(5):
        changed = False
        for name, (lower, upper) in PARAMETER_RANGES.items():
            current = parameters[name]
            best_value = current
            best_error = objective(runs, parameters)
            for value in range(lower, upper + 1):
                trial = dict(parameters)
                trial[name] = value
                error = objective(runs, trial)
                if error < best_error - 1e-12:
                    best_error = error
                    best_value = value
            if best_value != current:
                changed = True
                parameters[name] = best_value
        if not changed:
            break
    return parameters


def main() -> int:
    if len(sys.argv) != 3:
        print("REFUSED: expected input and output paths", file=sys.stderr)
        return 2
    try:
        runs, probe = parse_payload(Path(sys.argv[1]))
        parameters = calibrate(runs)
        projected = simulate(probe, parameters)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    result = {
        "status": "ok",
        "parameters": parameters,
        "probe": {
            key: round(float(value), 3)
            for key, value in sorted(projected.items())
        },
    }
    Path(sys.argv[2]).write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

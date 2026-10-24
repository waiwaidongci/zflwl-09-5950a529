from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path


ENTRY = os.environ.get("FAB_CALIBRATOR", "/app/fit_fab_sim.py")

SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
}
TRUE_PARAMETERS = {
    "service_a_sec": 35,
    "service_b_sec": 50,
    "buffer_capacity": 2,
    "rework_percent": 18,
}
OBSERVATION_KEYS = (
    "completed",
    "wip_end",
    "busy_a_sec",
    "busy_b_sec",
    "blocked_a_sec",
    "blocked_b_sec",
)


def _run(payload: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-S", "-P", "-B", ENTRY, str(payload), str(output)],
        text=True,
        capture_output=True,
        env=SAFE_ENV,
        timeout=60,
        check=False,
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _simulate(run: dict[str, object], parameters: dict[str, int]) -> dict[str, int]:
    total_lots = len(run["release_min"])
    release_index = 0
    a_queue: deque[int] = deque()
    b_buffer: deque[tuple[int, int]] = deque()
    a_active = None
    a_remaining = 0
    a_blocked = None
    b_active = None
    b_remaining = 0
    b_blocked = None
    completed = 0
    busy_a = 0
    busy_b = 0
    blocked_a = 0
    blocked_b = 0
    capacity = parameters["buffer_capacity"]
    for second in range(run["horizon_min"] * 60):
        minute = second // 60
        while (
            release_index < total_lots
            and run["release_min"][release_index] == minute
        ):
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
                roll = (run["seed"] * 31 + lot * 1009 + attempt * 9176) % 100
                if roll < parameters["rework_percent"] and attempt < 3:
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


def _releases() -> list[int]:
    return [0, 0, 1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 70, 90]


def _payload(run_count: int = 6) -> tuple[dict[str, object], dict[str, object], dict[str, int]]:
    runs = []
    for index in range(run_count):
        run = {
            "run_id": f"RUN-{index:03d}",
            "seed": 17 + index * 29,
            "horizon_min": 240,
            "release_min": [0] + [value + index for value in _releases()[1:]],
        }
        run["observations"] = _simulate(run, TRUE_PARAMETERS)
        runs.append(run)
    probe = {
        "seed": 411,
        "horizon_min": 300,
        "release_min": [0, 0, 2, 5, 9, 14, 20, 27, 35, 44, 54, 66],
    }
    expected_probe = {
        key: round(float(value), 3)
        for key, value in _simulate(probe, TRUE_PARAMETERS).items()
    }
    payload = {
        "schema": "fab-line-v1",
        "calibration_runs": runs,
        "probe": probe,
    }
    return payload, expected_probe, TRUE_PARAMETERS


def test_six_run_discrete_event_parameters_are_recovered(tmp_path: Path) -> None:
    """题面：
    「新试产线还没有完整的设备台账，工艺组只留下了几组放料计划和当时的逐分钟汇总。」
    「结果文件只能有 `status`、`parameters`、`probe`。`status` 写 `"ok"`；`parameters`」
    「测试目录，也不得写死运行号或具体参数。」
    """

    payload, _, expected = _payload()
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert result["status"] == "ok"
    assert result["parameters"] == expected, "CASE-SIM-01 CASE-RUN-01 CASE-OUTPUT-02 CASE-OUTPUT-03"


def test_probe_completions_follow_calibrated_rework(tmp_path: Path) -> None:
    """题面：
    「恰好包含上述四个参数名；`probe` 的六项汇总与输入中的 `observations` 同名，数值保留」
    """

    payload, expected, _ = _payload()
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert result["probe"]["completed"] == expected["completed"]
    assert result["probe"]["wip_end"] == expected["wip_end"], "CASE-INPUT-02 CASE-RUN-02 CASE-RUN-03"


def test_rework_probability_changes_backend_busy_time(tmp_path: Path) -> None:
    """题面：
    「`blocked_a_sec`、`blocked_b_sec`，六项均为非负整数。`probe` 只能有 `seed`、」
    """

    payload, expected, _ = _payload(7)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert result["parameters"]["rework_percent"] == TRUE_PARAMETERS["rework_percent"]
    assert result["probe"]["busy_b_sec"] == expected["busy_b_sec"], "CASE-REWORK-01 CASE-OBS-01 CASE-PARAM-01"


def test_finite_buffer_exposes_blocked_seconds(tmp_path: Path) -> None:
    """题面：
    「容纳 `buffer_capacity` 个批次。容量已满时 A 保持完工件不能放行，这段等待计入」
    """

    payload, expected, _ = _payload(6)
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert result["parameters"]["buffer_capacity"] == TRUE_PARAMETERS["buffer_capacity"]
    assert result["probe"]["blocked_a_sec"] == expected["blocked_a_sec"]
    assert result["probe"]["blocked_b_sec"] == expected["blocked_b_sec"], "CASE-SCHEMA-01 CASE-OBS-02 CASE-OBS-03 CASE-BUFFER-01"


def test_eight_release_minutes_are_accepted(tmp_path: Path) -> None:
    """题面：
    「`python3 /app/fit_fab_sim.py <input.json> <output.json>`。成功时不向标准输出或标准错误」
    """

    payload, _, _ = _payload(5)
    short = [0, 0, 3, 9, 18, 31, 49, 72]
    for index, run in enumerate(payload["calibration_runs"]):
        run["release_min"] = [0] + [value + index for value in short[1:]]
        run["observations"] = _simulate(run, TRUE_PARAMETERS)
    payload["probe"]["release_min"] = short
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert result["status"] == "ok"
    assert len(payload["calibration_runs"][0]["release_min"]) == 8, "CASE-RUNID-02 CASE-PROBE-01 CASE-SAFE-01 CASE-RELEASE-01"


def test_duplicate_run_id_is_refused(tmp_path: Path) -> None:
    """题面：
    「`calibration_runs` 是 5 到 8 组运行。每组只能含 `run_id`、`seed`、`horizon_min`、」
    """

    payload, _, _ = _payload()
    payload["calibration_runs"][1]["run_id"] = payload["calibration_runs"][0]["run_id"]
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists(), "CASE-RUNID-01"


def test_seven_release_minutes_are_refused(tmp_path: Path) -> None:
    """题面：
    「`observations` 只能有 `completed`、`wip_end`、`busy_a_sec`、`busy_b_sec`、」
    """

    payload, _, _ = _payload()
    payload["probe"]["release_min"] = [0, 3, 9, 18, 31, 49, 72]
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists(), "CASE-RELEASE-02"


def test_extra_top_level_key_is_refused(tmp_path: Path) -> None:
    """题面：
    「时间按整秒推进。A 设备一次处理一个批次，完工后进入 B 前缓冲区；缓冲区只能」
    """

    # CASE-INPUT-01 SIM-FAIL-04 SIM-FAIL-05
    payload, _, _ = _payload()
    payload["shift_note"] = "weekend"
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    assert completed.returncode == 2
    assert completed.stderr.startswith("REFUSED: ")
    assert not output.exists(), "CASE-INPUT-01"


def test_success_output_is_compact_sorted_and_newline_terminated(tmp_path: Path) -> None:
    """题面：
    「输入顶层只接受 `schema`、`calibration_runs`、`probe`。`schema` 必须为」
    """

    payload, _, _ = _payload()
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    raw = output.read_bytes()
    parsed = json.loads(raw)
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert completed.returncode == 0
    assert raw == canonical, "CASE-OUTPUT-01 CASE-SERIAL-01"


def test_gate_reports_all_probe_metrics(tmp_path: Path) -> None:
    """题面：
    「退出码为 2。落盘紧凑 JSON，字段名按字母升序，文件尾只留一个换行。候选程序不得读取」
    """

    payload, expected, _ = _payload()
    source = tmp_path / "input.json"
    output = tmp_path / "result.json"
    _write_json(source, payload)
    completed = _run(source, output)
    result = json.loads(output.read_text(encoding="utf-8"))
    assert completed.returncode == 0
    assert set(result["probe"]) == set(OBSERVATION_KEYS)
    assert set(result["parameters"]) == set(TRUE_PARAMETERS)
    assert result["probe"] == expected, "CASE-PROBEOUT-01"

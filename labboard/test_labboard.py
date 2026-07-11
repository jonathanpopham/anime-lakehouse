"""Smoke tests for labboard server — uses fake_gate.sh, never the real lattice."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "labboard" / "server.py"
FAKE_GATE = ROOT / "labboard" / "fixtures" / "fake_gate.sh"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server on port {port} did not start in {timeout}s")


@pytest.fixture()
def server(tmp_path):
    port = _free_port()
    env = {
        **os.environ,
        "GATE_SH": str(FAKE_GATE),
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PY), "--port", str(port)],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_server(port)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        runs_dir = ROOT / "data" / "labboard"
        if runs_dir.exists():
            shutil.rmtree(runs_dir)


def _get_json(port: int, path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def _post_json(port: int, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _read_sse(port: int, run_id: str, timeout: float = 10.0) -> list[dict]:
    url = f"http://127.0.0.1:{port}/api/stream?run_id={run_id}"
    req = urllib.request.Request(url)
    events: list[dict] = []
    deadline = time.monotonic() + timeout

    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = b""
        while time.monotonic() < deadline:
            chunk = r.read(1)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                for line in frame.split(b"\n"):
                    decoded = line.decode("utf-8", errors="replace")
                    if decoded.startswith("data: "):
                        try:
                            events.append(json.loads(decoded[6:]))
                        except json.JSONDecodeError:
                            pass
                if events and events[-1].get("event") == "run_end":
                    return events
    return events


class TestLabboard:
    def test_initial_runs_empty(self, server):
        runs = _get_json(server, "/api/runs")
        assert isinstance(runs, list)

    def test_run_and_stream(self, server):
        status, data = _post_json(server, "/api/run")
        assert status == 200
        assert "run_id" in data
        run_id = data["run_id"]

        events = _read_sse(server, run_id)
        assert len(events) > 0

        event_types = [e["event"] for e in events]
        assert event_types[0] == "run_start"
        assert event_types[-1] == "run_end"
        assert "gate_start" in event_types
        assert "gate_end" in event_types

        run_start = events[0]
        assert "gates" in run_start
        assert isinstance(run_start["gates"], list)

    def test_409_on_concurrent_run(self, server):
        status1, data1 = _post_json(server, "/api/run")
        assert status1 == 200

        time.sleep(0.05)
        status2, data2 = _post_json(server, "/api/run")
        assert status2 == 409
        assert "error" in data2

        _read_sse(server, data1["run_id"], timeout=10)

    def test_run_record_exists(self, server):
        _, data = _post_json(server, "/api/run")
        run_id = data["run_id"]
        _read_sse(server, run_id)

        time.sleep(0.5)
        runs = _get_json(server, "/api/runs")
        assert isinstance(runs, list)
        assert len(runs) >= 1
        found = any(r.get("run_id", "").endswith("-fake") for r in runs)
        assert found, f"run record not found in {runs}"

    def test_run_record_schema(self, server):
        _, data = _post_json(server, "/api/run")
        _read_sse(server, data["run_id"])

        time.sleep(0.5)
        runs = _get_json(server, "/api/runs")
        run = runs[0]
        assert "run_id" in run
        assert "status" in run
        assert "trigger" in run
        assert "gates" in run
        assert isinstance(run["gates"], list)
        for g in run["gates"]:
            assert "gate" in g
            assert "status" in g

    def test_event_order_sane(self, server):
        _, data = _post_json(server, "/api/run")
        events = _read_sse(server, data["run_id"])
        types = [e["event"] for e in events]

        assert types[0] == "run_start"
        assert types[-1] == "run_end"

        gate_starts = [i for i, t in enumerate(types) if t == "gate_start"]
        gate_ends = [i for i, t in enumerate(types) if t == "gate_end"]
        assert len(gate_starts) == len(gate_ends)
        for s, e in zip(gate_starts, gate_ends):
            assert s < e

    def test_html_serves(self, server):
        req = urllib.request.Request(f"http://127.0.0.1:{server}/")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            content = r.read().decode()
            assert "labboard" in content

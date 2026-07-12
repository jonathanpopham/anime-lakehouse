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

    golden_dir = ROOT / "evals" / "golden"
    golden_backup = {}
    for f in ["title_moods_golden.jsonl", "title_moods_golden.pass2.jsonl"]:
        p = golden_dir / f
        if p.exists():
            golden_backup[f] = p.read_text()

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
        for f in ["title_moods_golden.jsonl", "title_moods_golden.pass2.jsonl"]:
            p = golden_dir / f
            if f in golden_backup:
                p.write_text(golden_backup[f])
            elif p.exists():
                p.unlink()


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

    def test_bad_run_id_returns_400(self, server):
        bad_ids = [
            "../../../etc/passwd",
            "../../secrets",
            "not-a-valid-id",
            "20260711T193000Z-ZZZZ",
            "20260711T193000Z-abcd/../../x",
        ]
        for bad_id in bad_ids:
            from urllib.parse import quote
            url = f"http://127.0.0.1:{server}/api/stream?run_id={quote(bad_id)}"
            req = urllib.request.Request(url)
            try:
                urllib.request.urlopen(req, timeout=5)
                assert False, f"expected 400 for run_id={bad_id!r}"
            except urllib.error.HTTPError as e:
                assert e.code == 400, f"got {e.code} for run_id={bad_id!r}"

    def test_non_json_lines_not_forwarded(self, server):
        status, data = _post_json(server, "/api/run")
        assert status == 200
        run_id = data["run_id"]
        events = _read_sse(server, run_id)
        for ev in events:
            assert isinstance(ev, dict), "non-JSON line leaked into SSE stream"
            assert "event" in ev


class TestLabeling:
    def test_label_page_serves(self, server):
        req = urllib.request.Request(f"http://127.0.0.1:{server}/label")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert "label" in r.read().decode().lower()

    def test_label_titles_returns_data(self, server):
        data = _get_json(server, "/api/label/titles?mode=pass1")
        assert "titles" in data
        assert "mood_tags" in data
        assert len(data["titles"]) <= 50
        assert len(data["mood_tags"]) == 5
        if data["titles"]:
            t = data["titles"][0]
            assert "media_id" in t
            assert "title" in t
            assert "synopsis" in t

    def test_save_label_and_read_jsonl(self, server):
        data = _get_json(server, "/api/label/titles?mode=pass1")
        assert data["titles"], "no titles in warehouse"
        title = data["titles"][0]

        payload = json.dumps({
            "media_id": title["media_id"],
            "title": title["title"],
            "mood_tags": ["dark", "hype"],
            "mode": "pass1",
        }).encode()

        req = urllib.request.Request(
            f"http://127.0.0.1:{server}/api/label/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read())
            assert resp["ok"] is True
            assert resp["record"]["media_id"] == title["media_id"]
            assert resp["record"]["mood_tags"] == ["dark", "hype"]

        golden_path = ROOT / "evals" / "golden" / "title_moods_golden.jsonl"
        assert golden_path.exists(), "JSONL file not created"
        lines = golden_path.read_text().strip().splitlines()
        found = False
        for line in lines:
            obj = json.loads(line)
            if obj["media_id"] == title["media_id"]:
                assert obj["mood_tags"] == ["dark", "hype"]
                assert obj["title"] == title["title"]
                found = True
                break
        assert found, "label not found in JSONL"

    def test_save_label_validation(self, server):
        status, data = _post_json(server, "/api/label/save")
        assert status == 400

        payload = json.dumps({
            "media_id": 99999,
            "title": "test",
            "mood_tags": [],
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server}/api/label/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_save_label_updates_existing(self, server):
        data = _get_json(server, "/api/label/titles?mode=pass1")
        title = data["titles"][0]

        for tags in [["cozy"], ["melancholy", "absurd"]]:
            payload = json.dumps({
                "media_id": title["media_id"],
                "title": title["title"],
                "mood_tags": tags,
                "mode": "pass1",
            }).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{server}/api/label/save",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)

        golden_path = ROOT / "evals" / "golden" / "title_moods_golden.jsonl"
        lines = golden_path.read_text().strip().splitlines()
        matches = [json.loads(l) for l in lines if json.loads(l)["media_id"] == title["media_id"]]
        assert len(matches) == 1, "should deduplicate by media_id"
        assert matches[0]["mood_tags"] == ["absurd", "melancholy"]

    def test_pass2_writes_separate_file(self, server):
        data = _get_json(server, "/api/label/titles?mode=pass2")
        title = data["titles"][0]

        payload = json.dumps({
            "media_id": title["media_id"],
            "title": title["title"],
            "mood_tags": ["cozy", "dark"],
            "mode": "pass2",
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{server}/api/label/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)

        pass2_path = ROOT / "evals" / "golden" / "title_moods_golden.pass2.jsonl"
        assert pass2_path.exists()
        lines = pass2_path.read_text().strip().splitlines()
        assert any(json.loads(l)["media_id"] == title["media_id"] for l in lines)

    def test_report_endpoint(self, server):
        data = _get_json(server, "/api/label/report")
        assert "report" in data
        assert "summary" in data
        assert "pass1_count" in data["summary"]
        assert "pass2_count" in data["summary"]
        assert "avg_agreement" in data["summary"]

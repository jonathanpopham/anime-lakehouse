"""labboard: stdlib-only SSE server for the anime-lakehouse gate lattice.

Surface (default 127.0.0.1:8377):
  GET  /             → UI (labboard/index.html)
  GET  /api/runs     → JSON list of run records, newest first
  POST /api/run      → spawn gate.sh --robot --trigger labboard; 409 if live
  GET  /api/stream?run_id=X → SSE, one data: frame per NDJSON event
  GET  /label        → golden-set labeling page (pass 1)
  GET  /label/pass2  → second-pass labeling (shuffled, labels hidden)
  GET  /label/report → per-title agreement report
  GET  /api/label/titles?mode=pass1|pass2 → top 50 titles JSON
  POST /api/label/save → write label to JSONL
  GET  /api/label/report → agreement data JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import queue
import random
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = pathlib.Path(__file__).resolve().parent.parent
LABBOARD_DIR = pathlib.Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "labboard" / "runs"
LIVE_DIR = ROOT / "data" / "labboard" / "live"
GATE_SH = os.environ.get("GATE_SH", str(ROOT / "ci" / "gate.sh"))
HTML_PATH = LABBOARD_DIR / "index.html"
LABEL_HTML_PATH = LABBOARD_DIR / "label.html"
REPORT_HTML_PATH = LABBOARD_DIR / "report.html"
WAREHOUSE_PATH = ROOT / "transform" / "warehouse.duckdb"
GOLDEN_DIR = ROOT / "evals" / "golden"
GOLDEN_JSONL = GOLDEN_DIR / "title_moods_golden.jsonl"
GOLDEN_PASS2_JSONL = GOLDEN_DIR / "title_moods_golden.pass2.jsonl"
MOOD_TAGS = ["dark", "cozy", "hype", "melancholy", "absurd"]
_VALID_RUN_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{4,}$")


def _validate_run_id(run_id: str) -> bool:
    if not _VALID_RUN_ID.match(run_id):
        return False
    filename = f"{run_id}.ndjson"
    if os.sep in filename or "\0" in filename:
        return False
    return True


class RunManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live_run_id: str | None = None
        self._process: subprocess.Popen | None = None
        self._subscribers: dict[str, list[queue.Queue[str | None]]] = {}
        self._pump_thread: threading.Thread | None = None

    @property
    def live_run_id(self) -> str | None:
        with self._lock:
            return self._live_run_id

    def start_run(self) -> str:
        with self._lock:
            if self._live_run_id is not None:
                raise RuntimeError("run already in progress")

            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            short = uuid.uuid4().hex[:4]
            run_id = f"{ts}-{short}"
            self._live_run_id = run_id
            self._subscribers[run_id] = []

            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            RUNS_DIR.mkdir(parents=True, exist_ok=True)

            cmd = [GATE_SH, "--robot", "--trigger", "labboard"]
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            self._pump_thread = threading.Thread(
                target=self._pump, args=(run_id,), daemon=True
            )
            self._pump_thread.start()

        return run_id

    def _pump(self, run_id: str) -> None:
        live_path = LIVE_DIR / f"{run_id}.ndjson"
        proc = self._process
        assert proc is not None and proc.stdout is not None

        try:
            with open(live_path, "w") as live_file:
                for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    if not line:
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        sys.stderr.write(f"[labboard] skipping non-JSON line: {line[:120]}\n")
                        continue
                    live_file.write(line + "\n")
                    live_file.flush()

                    with self._lock:
                        for q in self._subscribers.get(run_id, []):
                            q.put(line)
        finally:
            proc.wait()
            with self._lock:
                for q in self._subscribers.get(run_id, []):
                    q.put(None)
                self._live_run_id = None
                self._process = None

    def subscribe(self, run_id: str) -> tuple[list[str], queue.Queue[str | None]]:
        q: queue.Queue[str | None] = queue.Queue()
        replay: list[str] = []
        live_path = LIVE_DIR / f"{run_id}.ndjson"

        with self._lock:
            if live_path.exists():
                replay = live_path.read_text().strip().splitlines()

            is_live = self._live_run_id == run_id
            if is_live:
                self._subscribers.setdefault(run_id, []).append(q)

        if not is_live:
            q.put(None)

        return replay, q

    def unsubscribe(self, run_id: str, q: queue.Queue[str | None]) -> None:
        with self._lock:
            subs = self._subscribers.get(run_id, [])
            if q in subs:
                subs.remove(q)

    def shutdown(self) -> None:
        with self._lock:
            if self._process is not None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()


_manager = RunManager()


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        _, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write(f"[labboard] {self.address_string()} {format % args}\n")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._serve_html()
        elif path == "/api/runs":
            self._serve_runs()
        elif path == "/api/stream":
            params = parse_qs(parsed.query)
            run_id = params.get("run_id", [None])[0]
            if not run_id:
                self._json_response(400, {"error": "run_id required"})
                return
            if not _validate_run_id(run_id):
                self._json_response(400, {"error": "invalid run_id"})
                return
            self._serve_stream(run_id)
        elif path == "/label":
            self._serve_file(LABEL_HTML_PATH)
        elif path == "/label/pass2":
            self._serve_file(LABEL_HTML_PATH)
        elif path == "/label/report":
            self._serve_file(REPORT_HTML_PATH)
        elif path == "/api/label/titles":
            params = parse_qs(parsed.query)
            mode = params.get("mode", ["pass1"])[0]
            self._serve_label_titles(mode)
        elif path == "/api/label/report":
            self._serve_label_report()
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/run":
            self._start_run()
        elif path == "/api/label/save":
            self._save_label()
        else:
            self._json_response(404, {"error": "not found"})

    def _serve_html(self) -> None:
        try:
            content = HTML_PATH.read_bytes()
        except FileNotFoundError:
            self._json_response(500, {"error": "index.html not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_runs(self) -> None:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        runs = []
        for p in sorted(RUNS_DIR.glob("*.json"), reverse=True):
            try:
                runs.append(json.loads(p.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        self._json_response(200, runs)

    def _start_run(self) -> None:
        try:
            run_id = _manager.start_run()
        except RuntimeError:
            self._json_response(409, {"error": "run already in progress"})
            return
        self._json_response(200, {"run_id": run_id})

    def _serve_stream(self, run_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        replay, sub_q = _manager.subscribe(run_id)

        try:
            for line in replay:
                self.wfile.write(f"data: {line}\n\n".encode())
                self.wfile.flush()

            while True:
                try:
                    event = sub_q.get(timeout=30)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                self.wfile.write(f"data: {event}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _manager.unsubscribe(run_id, sub_q)

    def _serve_file(self, path: pathlib.Path) -> None:
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            self._json_response(500, {"error": f"{path.name} not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_label_titles(self, mode: str) -> None:
        try:
            import duckdb
            con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
            rows = con.execute(
                "SELECT title_key, title, synopsis, popularity "
                "FROM dim_title ORDER BY popularity DESC LIMIT 50"
            ).fetchall()
            con.close()
        except Exception as e:
            self._json_response(500, {"error": f"warehouse query failed: {e}"})
            return

        titles = [
            {"media_id": r[0], "title": r[1], "synopsis": r[2] or "", "popularity": r[3]}
            for r in rows
        ]

        if mode == "pass2":
            seed = hashlib.sha256(b"pass2-shuffle").digest()
            rng = random.Random(int.from_bytes(seed[:8], "big"))
            rng.shuffle(titles)

        existing: dict[int, list[str]] = {}
        if mode == "pass1" and GOLDEN_JSONL.exists():
            for line in GOLDEN_JSONL.read_text().strip().splitlines():
                try:
                    obj = json.loads(line)
                    existing[obj["media_id"]] = obj["mood_tags"]
                except (json.JSONDecodeError, KeyError):
                    continue

        self._json_response(200, {
            "titles": titles,
            "mood_tags": MOOD_TAGS,
            "mode": mode,
            "existing": existing if mode == "pass1" else {},
        })

    def _save_label(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            self._json_response(400, {"error": "empty body"})
            return
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"error": "invalid JSON"})
            return

        media_id = data.get("media_id")
        title = data.get("title", "")
        mood_tags = data.get("mood_tags", [])
        mode = data.get("mode", "pass1")

        if not media_id:
            self._json_response(400, {"error": "media_id required"})
            return
        if not isinstance(mood_tags, list) or not (1 <= len(mood_tags) <= 3):
            self._json_response(400, {"error": "mood_tags must have 1-3 items"})
            return
        if not all(t in MOOD_TAGS for t in mood_tags):
            self._json_response(400, {"error": f"mood_tags must be from {MOOD_TAGS}"})
            return

        target = GOLDEN_PASS2_JSONL if mode == "pass2" else GOLDEN_JSONL
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

        record = {"media_id": media_id, "title": title, "mood_tags": sorted(mood_tags)}

        if target.exists():
            lines = target.read_text().strip().splitlines()
            updated = False
            for i, line in enumerate(lines):
                try:
                    obj = json.loads(line)
                    if obj.get("media_id") == media_id:
                        lines[i] = json.dumps(record, ensure_ascii=False)
                        updated = True
                        break
                except json.JSONDecodeError:
                    continue
            if updated:
                target.write_text("\n".join(lines) + "\n")
            else:
                with target.open("a") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            with target.open("w") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._json_response(200, {"ok": True, "record": record})

    def _serve_label_report(self) -> None:
        pass1: dict[int, dict] = {}
        pass2: dict[int, dict] = {}

        if GOLDEN_JSONL.exists():
            for line in GOLDEN_JSONL.read_text().strip().splitlines():
                try:
                    obj = json.loads(line)
                    pass1[obj["media_id"]] = obj
                except (json.JSONDecodeError, KeyError):
                    continue

        if GOLDEN_PASS2_JSONL.exists():
            for line in GOLDEN_PASS2_JSONL.read_text().strip().splitlines():
                try:
                    obj = json.loads(line)
                    pass2[obj["media_id"]] = obj
                except (json.JSONDecodeError, KeyError):
                    continue

        report = []
        all_ids = sorted(set(pass1.keys()) | set(pass2.keys()))
        for mid in all_ids:
            p1 = pass1.get(mid)
            p2 = pass2.get(mid)
            entry: dict = {"media_id": mid, "title": (p1 or p2 or {}).get("title", "")}
            entry["pass1_tags"] = p1["mood_tags"] if p1 else None
            entry["pass2_tags"] = p2["mood_tags"] if p2 else None
            if p1 and p2:
                s1, s2 = set(p1["mood_tags"]), set(p2["mood_tags"])
                entry["agreement"] = len(s1 & s2) / len(s1 | s2) if (s1 | s2) else 0.0
            else:
                entry["agreement"] = None
            report.append(entry)

        total = len(report)
        both = [r for r in report if r["agreement"] is not None]
        avg_agreement = sum(r["agreement"] for r in both) / len(both) if both else None

        self._json_response(200, {
            "report": report,
            "summary": {
                "total_titles": total,
                "pass1_count": len(pass1),
                "pass2_count": len(pass2),
                "both_count": len(both),
                "avg_agreement": round(avg_agreement, 4) if avg_agreement is not None else None,
            }
        })

    def _json_response(self, code: int, data: object) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="labboard gate-lattice server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8377)
    args = parser.parse_args()

    server = QuietServer((args.host, args.port), Handler)
    print(f"labboard listening on http://{args.host}:{args.port}", file=sys.stderr)

    def _shutdown(signum: int, frame: object) -> None:
        print("\nshutting down…", file=sys.stderr)
        _manager.shutdown()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    finally:
        _manager.shutdown()


if __name__ == "__main__":
    main()

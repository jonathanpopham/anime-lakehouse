"""labboard: stdlib-only SSE server for the anime-lakehouse gate lattice.

Surface (default 127.0.0.1:8377):
  GET  /             → UI (labboard/index.html)
  GET  /api/runs     → JSON list of run records, newest first
  POST /api/run      → spawn gate.sh --robot --trigger labboard; 409 if live
  GET  /api/stream?run_id=X → SSE, one data: frame per NDJSON event
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
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
                stderr=subprocess.STDOUT,
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
            self._serve_stream(run_id)
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/run":
            self._start_run()
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

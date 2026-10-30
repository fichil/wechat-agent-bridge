"""Own and supervise the two local development services, without touching existing ones."""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.ai_timeouts import AI_SHUTDOWN_GRACE_SECONDS


def stop():
    import psutil
    metadata = ROOT / ".local/framework/current.json"
    if not metadata.is_file():
        raise RuntimeError("Legacy launcher has no graceful-stop record; back up SQLite before a controlled stop.")
    value = json.loads(metadata.read_text(encoding="utf-8"))
    process = psutil.Process(value["pid"])
    if abs(process.create_time()-value["created"]) > .01 or not any(arg.endswith("dev_server.py") for arg in process.cmdline()):
        raise RuntimeError("Launcher identity changed; no action taken")
    target = Path(value["stop_file"]).resolve()
    if not target.is_relative_to((ROOT / ".local/framework").resolve()) or target.name != "stop.request":
        raise RuntimeError("Invalid stop-file path; no action taken")
    target.write_text("stop\n", encoding="utf-8")
    print("Graceful stop requested; the backend will finish active work and close SQLite.")
    return 0


def check():
    if os.name != "nt" or sys.maxsize <= 2**32:
        raise RuntimeError("Windows x64 is required")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Use Python 3.12 in .venv-backend")
    for module in ("fastapi", "uvicorn", "psutil", "cryptography", "zstandard", "PIL", "websockets"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"Missing backend dependency: {module}; install backend/requirements.lock.txt")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js was not found on PATH")
    version = subprocess.check_output([node, "--version"], text=True, creationflags=subprocess.CREATE_NO_WINDOW).strip()
    major, minor, *_ = map(int, version.lstrip("v").split("."))
    if not ((major == 22 and minor >= 12) or major >= 24):
        raise RuntimeError("Node.js 22.12+ or 24+ is required")
    vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
    if not vite.is_file():
        raise RuntimeError("Frontend dependencies missing; run npm ci in frontend")
    for port in (8765, 5173):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"Port {port} is already in use; existing service was left untouched") from None
    return node, vite


def main():
    os.environ.setdefault('BRIDGE_ENV', 'development')
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="Start, health-check and clean up; never scan WeChat")
    parser.add_argument("--stop", action="store_true", help="Gracefully stop the currently owned backend")
    args = parser.parse_args()
    try:
        if args.stop:
            return stop()
        node, vite = check()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.check:
        print("Prerequisites and ports OK")
        return 0
    from windows_job import WindowsJob
    logs = ROOT / ".local/framework" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6])
    logs.mkdir(parents=True)
    stop_file = logs / "stop.request"
    metadata = ROOT / ".local/framework/current.json"
    import psutil
    metadata.write_text(json.dumps(dict(pid=os.getpid(), created=psutil.Process().create_time(), stop_file=str(stop_file))), encoding="utf-8")
    children, handles = [], []
    job = WindowsJob()
    commands = [
        ("backend", ROOT, [sys.executable, "-B", str(ROOT / "scripts/serve_backend.py"), "--stop-file", str(stop_file)]),
        ("frontend", ROOT / "frontend", [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"]),
    ]
    try:
        for name, cwd, command in commands:
            output = (logs / f"{name}.log").open("wb")
            handles.append(output)
            child = subprocess.Popen([sys.executable, "-B", str(ROOT / "scripts/service_child.py"), *command],
                                     cwd=cwd, stdin=subprocess.PIPE, stdout=output, stderr=output,
                                     env={**os.environ, **({"BRIDGE_DISABLE_MONITOR": "1"} if args.smoke_test else {})},
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            children.append(child)
            job.assign(child)
            child.stdin.write(b"start\n")
            child.stdin.close()
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 40
        ready = False
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in children):
                raise RuntimeError(f"Service exited during startup. Logs: {logs}")
            try:
                with opener.open("http://127.0.0.1:8765/api/v1/health", timeout=1) as response:
                    healthy = json.load(response).get("service") == "wechat-agent-bridge"
                with opener.open("http://127.0.0.1:5173", timeout=1) as response:
                    ready = healthy and response.status == 200
                if ready:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        if not ready:
            raise RuntimeError(f"Startup timed out. Logs: {logs}")
        print("Ready: http://127.0.0.1:5173", flush=True)
        print(f"Logs: {logs}", flush=True)
        if args.smoke_test:
            print("Startup smoke test passed (no WeChat scan)")
            return 0
        if not args.no_browser:
            webbrowser.open("http://127.0.0.1:5173")
        print("Press Ctrl+C to stop this launch and its child processes.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        if stop_file.exists():
            return 0
        raise RuntimeError(f"A service stopped. Logs: {logs}")
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if children and children[0].poll() is None:
            stop_file.write_text("stop\n", encoding="utf-8")
            try:
                children[0].wait(timeout=AI_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                print("Backend graceful-stop deadline exceeded; the owned job will close.", file=sys.stderr)
        job.close()
        for child in children:
            # If assignment failed, the helper is still blocked on stdin and owns no service.
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for handle in handles:
            handle.close()
        if metadata.is_file() and json.loads(metadata.read_text(encoding="utf-8")).get("pid") == os.getpid():
            metadata.unlink()


if __name__ == "__main__":
    sys.exit(main())

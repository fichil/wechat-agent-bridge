"""Uvicorn owner with a graceful-stop file for hidden Windows launches."""
import argparse
from pathlib import Path
import sys
import threading
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-file", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database")
    args = parser.parse_args()
    from backend.app.main import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(database_path=args.database), host="127.0.0.1", port=args.port, workers=1, access_log=False))
    finished = threading.Event()
    def watch():
        while not finished.wait(.25):
            if Path(args.stop_file).is_file():
                server.should_exit = True
                return
    thread = threading.Thread(target=watch, name="graceful-stop", daemon=True)
    thread.start()
    try:
        server.run()
    finally:
        finished.set()
        thread.join(timeout=1)

if __name__ == "__main__":
    main()

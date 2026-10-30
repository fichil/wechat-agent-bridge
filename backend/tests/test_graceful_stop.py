"""Exercise the hidden-launch stop path using only an isolated empty database."""
from pathlib import Path
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import httpx

class GracefulStopTests(unittest.TestCase):
    def test_stop_file_closes_server_and_sqlite(self):
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temp:
            stop=Path(temp)/"stop.request"; database=Path(temp)/"isolated.sqlite3"
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            process=subprocess.Popen([sys.executable,str(root/"scripts/serve_backend.py"),"--stop-file",str(stop),"--database",str(database),"--port",str(port)],
                cwd=root,env={**os.environ,"BRIDGE_DISABLE_MONITOR":"1","BRIDGE_ENV":"development","BRIDGE_PUBLIC_ORIGIN":"http://127.0.0.1:5173","BRIDGE_AUTH_DATABASE":str(Path(temp)/'auth.sqlite3')},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            try:
                with httpx.Client(trust_env=False,timeout=.5) as client:
                    for _ in range(100):
                        try:
                            if client.get(f"http://127.0.0.1:{port}/api/v1/health").status_code==200:break
                        except httpx.HTTPError:pass
                        time.sleep(.05)
                    else:self.fail("isolated service did not start")
                stop.write_text("stop",encoding="utf-8")
                self.assertEqual(process.wait(timeout=15),0)
                db=sqlite3.connect(database)
                try:
                    self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0],"ok")
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],8)
                finally:db.close()
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)

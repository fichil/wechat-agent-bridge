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
    def test_disconnected_request_cannot_block_lifespan_cleanup(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0)); port = reserved.getsockname()[1]
            fixture = temp / 'fixture.py'
            fixture.write_text('''import asyncio, sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
import backend.app.main as backend
directory = Path(sys.argv[1])
@asynccontextmanager
async def lifespan(app):
    yield
    await asyncio.sleep(1)
    (directory / 'closed').write_text('lifespan completed')
def create_app(**unused):
    app = FastAPI(lifespan=lifespan)
    @app.get('/hold')
    async def hold():
        (directory / 'started').touch()
        try:
            await asyncio.Event().wait()
        finally:
            (directory / 'cancelled').touch()
    return app
backend.create_app = create_app
sys.argv = ['serve_backend', '--stop-file', str(directory / 'stop.request'), '--port', sys.argv[2]]
from scripts.serve_backend import main
main()
''', encoding='utf-8')
            process = subprocess.Popen([sys.executable, str(fixture), str(temp), str(port)], cwd=root,
                env={**os.environ, 'PYTHONPATH': str(root)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                for _ in range(150):
                    try:
                        connection = socket.create_connection(('127.0.0.1', port), timeout=.5)
                        break
                    except OSError: time.sleep(.05)
                else: self.fail('isolated HTTP fixture did not start')
                try:
                    connection.sendall(b'GET /hold HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
                    for _ in range(100):
                        if (temp / 'started').exists(): break
                        time.sleep(.05)
                    else: self.fail('isolated request did not start')
                finally: connection.close()
                (temp / 'stop.request').touch()
                self.assertEqual(process.wait(timeout=25), 0)
                self.assertTrue((temp / 'cancelled').exists())
                self.assertEqual((temp / 'closed').read_text(), 'lifespan completed')
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)

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
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],12)
                finally:db.close()
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=5)

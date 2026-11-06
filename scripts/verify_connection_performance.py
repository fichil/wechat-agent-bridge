"""Concurrent HTTP acceptance against disposable production-sized synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'backend/tests'))
from auth_support import TestClient, create_app
from backend.app.services import browser_values
from backend.app.storage import dump, now
from backend.app.log_storage import pack
from test_conversations import accounts, Reader, rows
from test_message_api import message


def verify():
    with tempfile.TemporaryDirectory(prefix='bridge-connection-performance-') as directory:
        app = create_app(probe=accounts, conversation_reader=Reader(), monitor=False,
                         ai_auto=False, send_auto=False, database_path=Path(directory)/'synthetic.sqlite3')
        with TestClient(app, base_url='http://127.0.0.1:8765') as client:
            store, bridge = app.state.store, app.state.bridge
            snapshot = store.save_accounts(dict(browser_values(accounts()), scan_id='synthetic', scanned_at=now()), publish=bridge.discovery.publish)
            aid = snapshot['accounts'][0]['account_id']
            source = rows(10)
            for index, item in enumerate(source):
                item.update(category='group' if index % 2 else 'contact', is_container=False,
                            source_id='synthetic_'+str(index)+('@chatroom' if index % 2 else ''))
            store.save_conversations(aid, dict(items=source, status='complete', issues=[]))
            cids = store.call(lambda: [r[0] for r in store.db.execute('SELECT conversation_id FROM conversations WHERE account_id=?', (aid,))])
            for index, cid in enumerate(cids):
                items = [message(1+index*260+n) for n in range(260)]
                for item in items[:30]: item.update(type='image', content=dict(description='合成图片'))
                store.save_history(aid, cid, dict(items=items, has_more=False, issues=[], status='complete'), {})

            blob = b'synthetic image metadata performance\0'.ljust(400*1024, b'x')
            detail = pack(dict(synthetic='x'*(20*1024)))
            def seed():
                with store.db:
                    for mid, in store.db.execute("SELECT message_id FROM messages WHERE json_extract(payload,'$.type')='image'").fetchall():
                        store.db.execute("INSERT INTO media_cache VALUES(?,?,'image','image/png',?,?,?,NULL,0)",
                                         ('image:'+mid, aid, blob, len(blob), hashlib.sha256(blob).hexdigest()))
                    for index in range(17000):
                        identity = 'synthetic-log-'+str(index)
                        summary = dict(log_id=identity, category='source', status='completed', operation='synthetic', count=1, updated_at=now())
                        store.db.execute("INSERT INTO diagnostic_logs VALUES(?,? ,NULL,'source',NULL,?,?,'completed',0,NULL,?)",
                                         (identity, aid, now(), now(), pack(summary)))
                        store.db.execute('INSERT INTO diagnostic_details VALUES(?,?,?)', (identity, detail, len(detail)))
                    store.db.execute('INSERT INTO compatibility_state VALUES(?,1,?,0,?,?)',
                                     (aid, 'synthetic', '', dump(dict(send=dict(ready=True), capabilities={}))))
            store.call(seed)

            # A metadata read must remain independent of total image BLOB size.
            def metadata():
                import psutil
                process = psutil.Process()
                keys = store.db.execute("SELECT cache_key FROM media_cache WHERE kind='image'").fetchall()
                store.db.execute('PRAGMA shrink_memory')
                before = process.io_counters()
                for key, in keys:
                    store.db.execute('SELECT digest,mime_type FROM media_cache INDEXED BY media_cache_metadata WHERE cache_key=? AND account_id=?', (key, aid)).fetchone()
                reads = process.io_counters().read_bytes-before.read_bytes
                assert reads < 2*1024*1024, reads
                return dict(images=len(keys), image_bytes=len(keys)*len(blob), metadata_read_bytes=reads)
            result = store.call(metadata)
            stopped = threading.Event()
            def load():
                while not stopped.is_set():
                    store.call(lambda: store.db.execute('PRAGMA shrink_memory').fetchall())
                    store.conversations(aid)
                    store.log_write(dict(category='source', operation='synthetic', account_id=aid), dict(synthetic='x'*20000), 'synthetic-load')
                    stopped.wait(.01)
            samples = []
            with ThreadPoolExecutor(max_workers=1) as executor:
                background = executor.submit(load)
                try:
                    for _ in range(30):
                        for path in ('accounts/query', 'compatibility/query'):
                            started = time.monotonic()
                            response = client.post('/api/v1/'+path, json={})
                            elapsed = time.monotonic()-started
                            assert response.status_code == 200, response.text
                            samples.append(elapsed)
                finally:
                    stopped.set()
                background.result()
            ordered = sorted(samples)
            result.update(messages=2600, diagnostic_rows=17000, diagnostic_bytes=17000*len(detail),
                          samples=len(samples), p95_ms=round(ordered[int(len(ordered)*.95)-1]*1000, 3),
                          max_ms=round(max(samples)*1000, 3), schema=store.call(lambda: store.db.execute('PRAGMA user_version').fetchone()[0]))
            assert result['p95_ms'] < 1000 and result['max_ms'] < 3000, result
            return result


if __name__ == '__main__': print(json.dumps(verify(), ensure_ascii=False, indent=2))

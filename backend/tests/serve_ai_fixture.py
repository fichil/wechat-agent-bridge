"""Isolated browser integration fixture. Never opens the user's archive or WeChat."""
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from datetime import datetime, timezone, timedelta
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from backend.app.main import create_app, ALLOWED_ORIGINS
from backend.app.services import browser_values
from test_conversations import accounts, rows, Reader
from ai_fakes import FakeProvider
from test_message_api import message
from backend.app.ai_execution_models import DEFAULT_INSTRUCTIONS
from backend.app.diagnostics import record_source

class BrowserProvider(FakeProvider):
    fail_next = False

    def respond(self, request):
        if self.fail_next:
            self.fail_next = False
            return httpx.Response(503, text="synthetic provider unavailable")
        return super().respond(request)

ALLOWED_ORIGINS.add("http://127.0.0.1:15173")
reader = Reader()
provider = BrowserProvider()
class BrowserSender:
    result = 'sent'
    def inspect(self, binding): return dict(ready=True)
    def send(self, binding, job): return dict(status=self.result,code='synthetic')

sender = BrowserSender()
app = create_app(accounts, database_path=":memory:", conversation_reader=reader, message_reader=reader, monitor=False, ai_provider=provider,send_transport=sender,send_auto=True)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        store = application.state.store
        application.state.discovery.scan()
        application.state.test_ids = []
        for account in store.save_accounts(browser_values(accounts()))["accounts"]:
            aid = account["account_id"]
            store.save_conversations(aid, dict(items=rows(1), status="complete", issues=[]))
            application.state.test_ids.append(dict(account_id=aid, conversation_id=store.conversations(aid)["items"][0]["conversation_id"]))
        yield


app.router.lifespan_context = lifespan


@app.post("/test/reset")
def reset():
    store = app.state.store
    assert store.path == ":memory:"
    provider.fail_next = False
    sender.result = 'sent'
    def clear():
        with store.db:
            for table in ('delivery_attempts','deliveries','reply_triggers','delivery_sessions'):
                store.db.execute('DELETE FROM '+table)
            store.db.execute('DELETE FROM sync_state')
            store.db.execute("DELETE FROM diagnostic_logs")
            store.db.execute("DELETE FROM api_tasks")
            store.db.execute("DELETE FROM ai_run_messages")
            store.db.execute("DELETE FROM ai_runs")
            store.db.execute("DELETE FROM ai_sessions")
            store.db.execute("DELETE FROM ai_message_state")
            store.db.execute("DELETE FROM messages")
            store.db.execute("DELETE FROM history_ranges")
            store.db.execute("UPDATE ai_settings SET instructions=?,revision=0", (DEFAULT_INSTRUCTIONS,))
            store.db.execute("DELETE FROM conversation_preferences")
            store.db.execute("UPDATE ai_defaults SET role_card_code=NULL,api_profile_id=NULL,revision=0")
            store.db.execute("DELETE FROM api_profiles")
            store.db.execute("DELETE FROM role_cards")
    store.call(clear)
    return app.state.test_ids


@app.post("/test/message")
def insert_message(body: dict):
    store = app.state.store
    assert store.path == ":memory:"
    identity = app.state.test_ids[body.get("account", 0)]
    value = message(body.get("index", 1))
    value.update(is_self=False, type=body.get("type", "text"), content=body.get("content", {"text": "合成新消息"}))
    if body.get('historical'):
        store.save_history(identity["account_id"], identity["conversation_id"], dict(items=[value], has_more=False, issues=[], status="complete"), {})
    else:
        value['sent_at']=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
        value['_ref']['generation']='synthetic'
        source=store.conversation(identity['account_id'],identity['conversation_id'])['source_id']
        store.save_delta(identity['account_id'],dict(groups=[dict(shard=value['_ref']['shard'],table_name='Msg_test',source_id=source,generation='synthetic',last_id=value['_ref']['local_id'],items=[value])],issues=[]))
    return {"ok": True}


@app.post('/test/send-result')
def send_result(body: dict):
    assert app.state.store.path == ':memory:'
    assert body['status'] in ('sent','failed','unknown')
    sender.result=body['status']
    return {'ok':True}


@app.post("/test/fail-next")
def fail_next():
    assert app.state.store.path == ":memory:"
    provider.fail_next = True
    return {"ok": True}


@app.post("/test/source-logs")
def source_logs(body: dict):
    store = app.state.store
    assert store.path == ":memory:"
    identity = app.state.test_ids[body.get("account", 0)]
    for index in range(min(body.get("count", 1), 100)):
        record_source(store,"messages",dict(page=index),dict(items=[dict(text="合成读取记录", index=index)]),aid=identity["account_id"],cid=identity["conversation_id"],source="cache")
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18765, access_log=False)

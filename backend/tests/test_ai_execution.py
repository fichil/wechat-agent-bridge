import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import io
import json
import sqlite3
import threading
import time
from unittest.mock import patch
from uuid import uuid4
import httpx
from PIL import Image
from backend.app.ai_provider import Provider, build_request, parse_response, normalize_image
from backend.app.ai_execution_storage import EXECUTION_SCHEMA
from backend.app.storage import Store
from backend.app.conversations import ConversationError
from test_roles import RoleTestCase
from test_message_api import message
from migration_helpers import remove_v5

class ExecutionTests(RoleTestCase):
    def configured(self):
        self.create()
        self.profile = self.create_api()
        self.assertEqual(self.update(ai_enabled=True).status_code, 200)

    def ingest(self, index=1, account=0, **changes):
        item = {**message(index), "is_self": False, **changes}
        self.store.save_history(self.ids[account], self.cids[account], dict(items=[item], has_more=False, issues=[], status="complete"), {})
        return self.store.messages(self.ids[account], self.cids[account])["items"][-1]

    def start(self, account=0, request_id=None):
        return self.app.state.ai.start(self.ids[account], self.cids[account], request_id or uuid4().hex)

    def wait_run(self, run):
        for _ in range(500):
            value = self.store.ai_run(run["account_id"], run["conversation_id"], run["run_id"])
            if value["status"] not in ("queued", "running"):
                return value
            time.sleep(.005)
        self.fail("AI run timeout")

    def test_incremental_json_memory_reply_and_account_isolation(self):
        self.configured()
        self.ingest(1)
        first = self.wait_run(self.start())
        self.assertEqual(first["status"], "completed", first)
        self.assertIn(first["reply"], self.preferences()["memory"])
        self.assertEqual(self.preferences()["memory_revision"], 1)
        self.assertEqual(self.preferences(1)["memory"], "")
        page = self.store.messages(self.ids[0], self.cids[0])
        self.assertEqual(page["items"][0]["ai_state"]["status"], "memorized")
        self.ingest(2, content={"text": "新增消息"})
        second = self.wait_run(self.start())
        envelope = json.loads(self.provider.requests[-1]["input"][0]["content"][0]["text"])
        self.assertEqual(len(envelope["messages"]), 1)
        self.assertEqual(envelope["messages"][0]["content"]["text"], "新增消息")
        self.assertIn("合成历史", envelope["memory"])
        self.assertTrue(envelope["messages"][0]["sent_at"])
        self.assertIn("instructions", envelope)
        self.assertIn("character_card", envelope)
        self.assertEqual(second["memory_revision"], 2)
        count = len(self.provider.requests)
        self.assertEqual(self.wait_run(self.start())["status"], "empty")
        self.assertEqual(len(self.provider.requests), count)
        wrong = self.post("ai-runs/query", account_id=self.ids[1], conversation_id=self.cids[1], run_id=first["run_id"])
        self.assertEqual(wrong.status_code, 404)

    def test_message_content_change_and_late_transcript_are_not_lost(self):
        self.configured()
        self.ingest(1, type="voice", content={"transcript": None})
        self.assertEqual(self.wait_run(self.start())["status"], "empty")
        self.assertEqual(self.store.messages(self.ids[0], self.cids[0])["items"][0]["ai_state"]["status"], "waiting_transcript")
        self.ingest(1, type="voice", content={"transcript": "语音转写"})
        self.assertEqual(self.wait_run(self.start())["status"], "completed")
        self.ingest(1, type="voice", content={"transcript": "修正后的语音"})
        self.assertEqual(self.wait_run(self.start())["status"], "completed")
        self.assertIn("修正后的语音", self.preferences()["memory"])

    def test_new_message_and_changed_message_during_request_stay_pending(self):
        self.configured(); self.ingest(1)
        entered, release = threading.Event(), threading.Event()
        generate = self.provider.generate
        def blocked(*args):
            entered.set(); self.assertTrue(release.wait(5)); return generate(*args)
        with patch.object(self.provider, "generate", side_effect=blocked):
            run = self.start(); self.assertTrue(entered.wait(3))
            self.ingest(1, content={"text": "请求期间修改"}); self.ingest(2)
            release.set(); self.assertEqual(self.wait_run(run)["status"], "completed")
        self.assertEqual([m["ai_state"]["status"] for m in self.store.messages(self.ids[0], self.cids[0])["items"]], ["pending", "pending"])

    def test_manual_memory_edit_conflicts_and_disable_cancels_without_overwrite(self):
        self.configured(); self.ingest()
        for action in ("memory", "disable"):
            entered, release = threading.Event(), threading.Event()
            generate = self.provider.generate
            def blocked(*args):
                entered.set(); self.assertTrue(release.wait(5)); return generate(*args)
            with patch.object(self.provider, "generate", side_effect=blocked):
                run = self.start(); self.assertTrue(entered.wait(3))
                prefs = self.preferences()
                result = self.update(prefs["revision"], **({"memory": "手工保留"} if action == "memory" else {"ai_enabled": False}))
                self.assertEqual(result.status_code, 200)
                release.set()
                state = self.wait_run(run)["status"]
                self.assertEqual(state, "conflicted" if action == "memory" else "cancelled")
            self.assertEqual(self.preferences()["memory"], "手工保留")

    def test_failure_pauses_auto_and_retry_uses_latest_memory(self):
        self.configured(); self.ingest()
        with patch.object(self.provider, "generate", side_effect=ConversationError("ai_network_error", "合成超时", 422)):
            failed = self.wait_run(self.start())
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.preferences()["memory"], "")
        self.assertEqual(self.store.ai_candidates(), [])
        retry = self.app.state.ai.retry(self.ids[0], self.cids[0], failed["run_id"], "retry-once")
        self.assertEqual(self.wait_run(retry)["status"], "completed")
        self.assertFalse(self.store.ai_runs(self.ids[0], self.cids[0])["paused"])

    def test_repeated_request_and_parallel_click_share_one_run(self):
        self.configured(); self.ingest()
        with ThreadPoolExecutor(2) as workers:
            runs = list(workers.map(lambda _: self.start(request_id="same"), range(2)))
        self.assertEqual(runs[0]["run_id"], runs[1]["run_id"])
        self.assertEqual(self.wait_run(runs[0])["status"], "completed")
        count = len(self.provider.requests)
        self.assertEqual(self.start(request_id="same")["status"], "completed")
        self.assertEqual(len(self.provider.requests), count)

    def test_text_only_profile_skips_image_until_vision_is_verified(self):
        self.configured()
        self.store.save_verification(self.profile["profile_id"], 1, dict(status="passed", vision="unsupported", output_mode="schema"))
        image = self.ingest(1, type="sticker", content={})
        output = io.BytesIO(); Image.new("RGB", (8,8), "red").save(output, "PNG")
        self.store.image(self.ids[0], self.cids[0], image["message_id"], dict(status="available", mime_type="image/png", data_base64=base64.b64encode(output.getvalue()).decode()))
        self.ingest(2)
        self.assertEqual(self.wait_run(self.start())["message_count"], 1)
        self.store.save_verification(self.profile["profile_id"], 1, dict(status="passed", vision="supported", output_mode="schema"))
        self.assertEqual(self.wait_run(self.start())["status"], "completed")
        content = self.provider.requests[-1]["input"][0]["content"]
        self.assertTrue(content[-1]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("data_base64", content[0]["text"])

    def test_batches_and_input_limits_do_not_mark_omitted_messages(self):
        self.configured()
        for index in range(1,54): self.ingest(index)
        first = self.wait_run(self.start())
        self.assertEqual(first["message_count"], 50)
        self.assertEqual(self.store.ai_runs(self.ids[0],self.cids[0])["pending_count"], 3)
        self.assertEqual(self.wait_run(self.start())["message_count"], 3)
        self.ingest(54, content={"text": "超"*30000})
        count = len(self.provider.requests)
        failed = self.wait_run(self.start())
        self.assertEqual(failed["error"], "ai_input_too_large")
        self.assertEqual(len(self.provider.requests), count)

    def test_global_prompt_revision_and_each_request_reads_latest(self):
        self.configured(); self.ingest()
        value = self.post("ai-settings/query").json()
        self.assertEqual(self.post("ai-settings/update", instructions="全账号最新说明", expected_revision=value["revision"]).status_code, 200)
        self.assertEqual(self.post("ai-settings/update", instructions="旧页面", expected_revision=value["revision"]).status_code, 409)
        self.wait_run(self.start())
        self.assertIn("全账号最新说明", self.provider.requests[-1]["instructions"])

    def test_failed_save_preserves_old_config_and_no_secret_in_job_or_error(self):
        profile = self.create_api()
        fields = {k: profile[k] for k in ("name", "provider", "protocol", "base_url", "model", "anthropic_version", "workspace_id")}
        fields["model"] = "invalid-model"
        response = self.post("api-profiles/update", **fields, profile_id=profile["profile_id"], expected_revision=1, api_key="secret-synthetic-new")
        task = response.json()
        for _ in range(200):
            task = self.store.api_task(task["task_id"])
            if task["status"] == "failed": break
            time.sleep(.005)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(self.store.api_profile(profile["profile_id"])["model"], "synthetic-model")
        self.assertNotIn("secret-synthetic-new", json.dumps(task))

    def test_v4_migration_atomic_and_running_recovery(self):
        self.configured(); self.ingest()
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db: remove_v5(db)
        with patch("backend.app.storage.EXECUTION_SCHEMA", (*EXECUTION_SCHEMA, "INVALID SQL")):
            with self.assertRaises(ConversationError): Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='ai_settings'").fetchone()[0], 0)
        with closing(Store(self.path)) as reopened:
            self.assertFalse(reopened.preferences(self.ids[0],self.cids[0])["effective"]["ready"])
            reopened.save_verification(self.profile["profile_id"],1,dict(status="passed",vision="unverified",output_mode="schema"))
            run = reopened.start_ai_run(self.ids[0],self.cids[0],"interrupted")
            reopened.claim_ai_run(self.ids[0],self.cids[0],run["run_id"])
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.ai_run(self.ids[0],self.cids[0],run["run_id"])["error"], "ai_interrupted")
            self.assertEqual(reopened.preferences(self.ids[0],self.cids[0])["memory"], "")

    def test_protocol_errors_are_redacted_and_structured_fallback_is_narrow(self):
        profile = self.create_api()
        envelope = dict(request_id="x", instructions="json", character_card={}, memory="", messages=[])
        for protocol, bad in (("openai_responses", {"status":"incomplete"}), ("openai_chat_completions", {"choices":[{"finish_reason":"length"}]}),
                              ("anthropic_messages", {"stop_reason":"max_tokens"}), ("gemini_generate_content", {"candidates":[{"finishReason":"SAFETY"}]})):
            with self.assertRaises(ConversationError): parse_response(protocol,bad)
        provider = Provider(httpx.MockTransport(lambda req: httpx.Response(401, text="secret-in-error")))
        with self.assertRaises(ConversationError) as error: provider.generate(profile,"secret-in-error",envelope,"schema")
        self.assertNotIn("secret-in-error", str(error.exception))
        self.assertEqual(error.exception.code,"ai_provider_error")

    def test_automatic_trigger_initial_history_self_message_and_global_limit(self):
        self.create(); self.profile = self.create_api()
        self.ingest()
        self.update(ai_enabled=True)
        # Global invalidation advances cursors but must not change initial-history classification.
        self.store.save_verification(self.profile["profile_id"],1,self.profile["verification"])
        self.app.state.ai.tick()
        run = self.store.ai_runs(self.ids[0],self.cids[0])["items"][0]
        self.assertTrue(self.wait_run(run)["historical"])
        # Drain the automatic continuation, then a self-only message must wait.
        self.app.state.ai.tick()
        latest = self.store.ai_runs(self.ids[0],self.cids[0])["items"][0]
        self.wait_run(latest)
        count = len(self.provider.requests)
        self.ingest(2,is_self=True)
        self.app.state.ai.tick()
        self.assertEqual(len(self.provider.requests),count)
        self.ingest(3)
        self.app.state.ai.tick()
        run = self.store.ai_runs(self.ids[0],self.cids[0])["items"][0]
        self.assertEqual(self.wait_run(run)["message_count"],2)

    def test_only_explicit_format_rejection_downgrades_and_vision_is_optional(self):
        profile = self.create_api()
        from ai_fakes import FakeProvider
        fake, calls = FakeProvider(), []
        def handler(request):
            body=json.loads(request.content); calls.append(body)
            content=body["input"][0]["content"]
            if any(part["type"]=="input_image" for part in content):
                return httpx.Response(400,json={"error":"image input unsupported"})
            if body.get("text",{}).get("format",{}).get("type")=="json_schema":
                return httpx.Response(400,json={"error":"text.format json_schema not supported"})
            return fake.respond(request)
        result=Provider(httpx.MockTransport(handler)).test(profile,"synthetic", "JSON memory reply")
        self.assertEqual(result["status"],"passed")
        self.assertEqual(result["vision"],"unsupported")
        self.assertEqual(result["output_mode"],"json")
        self.assertEqual(len(calls),3)

    def test_profile_test_revision_conflict_cannot_commit_stale_candidate(self):
        profile=self.create_api()
        fields={k:profile[k] for k in ("name","provider","protocol","base_url","model","anthropic_version","workspace_id")}
        entered,release=threading.Event(),threading.Event()
        original=self.provider.test
        def blocked(*args):
            entered.set(); self.assertTrue(release.wait(5)); return original(*args)
        with patch.object(self.provider,"test",side_effect=blocked):
            task=self.app.state.ai.start_profile({**fields,"name":"stale candidate"},identity=profile["profile_id"],expected=1)
            self.assertTrue(entered.wait(3))
            self.store.save_api_profile({**fields,"name":"concurrent change"},identity=profile["profile_id"],expected_revision=1,verification=profile["verification"])
            release.set()
            for _ in range(300):
                task=self.store.api_task(task["task_id"])
                if task["status"] in ("failed","completed"):break
                time.sleep(.005)
        self.assertEqual(task["error"],"revision_conflict")
        self.assertEqual(self.store.api_profile(profile["profile_id"])["name"],"concurrent change")

    def test_schema_rejects_duplicate_keys_extra_fields_and_non_string_memory(self):
        for raw in ('{"memory":"a","memory":"b","reply":"x"}', '{"memory":3,"reply":"x"}',
                    '{"memory":"a","reply":"x","extra":true}', '```json\n{"memory":"a","reply":"x"}\n```'):
            with self.assertRaises(ConversationError):
                parse_response("openai_chat_completions",{"choices":[{"finish_reason":"stop","message":{"content":raw}}]})

    def test_two_accounts_run_concurrently_but_model_calls_never_exceed_two(self):
        self.configured();self.update(index=1,ai_enabled=True)
        self.ingest(account=0);self.ingest(account=1)
        lock,release=threading.Lock(),threading.Event()
        counts={"active":0,"maximum":0}; original=self.provider.generate
        def blocked(*args):
            with lock:
                counts["active"]+=1;counts["maximum"]=max(counts["maximum"],counts["active"])
            self.assertTrue(release.wait(5))
            try:return original(*args)
            finally:
                with lock:counts["active"]-=1
        with patch.object(self.provider,"generate",side_effect=blocked):
            one,two=self.start(0),self.start(1)
            for _ in range(200):
                if counts["active"]==2:break
                time.sleep(.005)
            task=self.app.state.ai.start_profile(identity=self.profile["profile_id"],expected=1)
            self.assertEqual(counts["active"],2)
            release.set();self.wait_run(one);self.wait_run(two)
            for _ in range(200):
                task=self.store.api_task(task["task_id"])
                if task["status"]=="completed":break
                time.sleep(.005)
        self.assertEqual(task["status"],"completed")
        self.assertEqual(counts["maximum"],2)

from migration_helpers import remove_v5
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from unittest.mock import patch

from backend.app.ai_models import PRESETS
from backend.app.conversations import ConversationError
from backend.app.storage import Store, AI_SCHEMA
from backend.app import secret_protection
from test_roles import RoleTestCase


class AiConfigurationTests(RoleTestCase):
    def default(self, **values):
        current = self.post("ai-defaults/query").json()
        return self.post("ai-defaults/update", expected_revision=current["revision"], **values)

    def edit_api(self, profile, **changes):
        fields = {key: profile[key] for key in ("name", "provider", "protocol", "base_url", "model", "anthropic_version", "workspace_id")}
        return self.finish_profile(self.post("api-profiles/update", profile_id=profile["profile_id"], expected_revision=profile["revision"], **{**fields, **changes}))

    def test_all_provider_protocol_presets_roundtrip_without_network(self):
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("No provider network allowed")))
        for preset in PRESETS:
            for option in preset["options"]:
                with self.subTest(provider=preset["provider"], protocol=option["protocol"]):
                    result = self.create_api(provider=preset["provider"], protocol=option["protocol"], base_url=option["base_url"] or "http://127.0.0.1:7777/v1")
                    self.assertTrue(result["key_readable"])
                    self.assertEqual(result["protocol"], option["protocol"])
                    self.assertNotIn("api_key", result)
                    self.assertNotIn("secret", result)
        self.assertEqual(self.post("api-profiles/query").json()["presets"], PRESETS)

    def test_dpapi_ciphertext_key_rotation_and_no_plaintext_responses(self):
        events = []; self.store.notify = events.append
        profile = self.create_api()
        ciphertext = lambda: self.store.call(lambda: self.store.db.execute("SELECT secret FROM api_profiles WHERE profile_id=?", (profile["profile_id"],)).fetchone()[0])
        first = ciphertext()
        self.assertNotIn(b"synthetic-test-only", first)
        self.assertTrue(secret_protection.verify(first))
        saved = self.edit_api(profile, name="新名称").json()
        self.assertEqual(ciphertext(), first)
        rotated = self.edit_api(saved, api_key="synthetic-replacement-only").json()
        self.assertNotEqual(ciphertext(), first)
        self.assertTrue(rotated["key_readable"])
        for response in (self.post("api-profiles/query"), self.post("api-profiles/get", profile_id=profile["profile_id"])):
            self.assertNotIn("synthetic-test-only", response.text)
            self.assertNotIn("synthetic-replacement-only", response.text)
        self.assertNotIn("synthetic", json.dumps(events))
        for value in (None, "", " ", "bad\nkey"):
            response = self.edit_api(rotated, api_key=value)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn("bad", response.text)
        self.assertEqual(self.edit_api(profile, name="stale").json()["code"], "revision_conflict")

    def test_invalid_urls_and_protocols_never_save(self):
        fields = dict(name="test", provider="openai", protocol="openai_responses", base_url="https://example.test/v1", model="model", api_key="synthetic-only")
        for url in ("https://user:pass@example.test/v1", "https://example.test/v1?key=secret", "https://example.test/#secret", "file:///secret", "https://example.test/sk-test", "https://example.test/%3Fkey=secret", "http://example.test:bad", "https://example.test/a b"):
            self.assertEqual(self.post("api-profiles/create", **{**fields, "base_url": url}).status_code, 422)
        for change in ({"name": " "}, {"model": " "}, {"api_key": " "}, {"provider": "unknown"}, {"protocol": "anthropic_messages"}):
            self.assertEqual(self.post("api-profiles/create", **{**fields, **change}).status_code, 422)
        self.assertEqual(self.post("api-profiles/query").json()["items"], [])

    def test_first_defaults_dynamic_inheritance_and_explicit_independent_overrides(self):
        first, second = self.create(), self.create()
        one, two = self.create_api(), self.create_api(name="第二 API")
        defaults = self.post("ai-defaults/query").json()
        self.assertEqual((defaults["role_card_code"], defaults["api_profile_id"]), (first["card_code"], one["profile_id"]))
        enabled = self.update(ai_enabled=True).json()
        self.assertIsNone(enabled["role_card_code"])
        self.assertIsNone(enabled["api_profile_id"])
        self.assertTrue(enabled["effective"]["ready"])
        self.assertEqual(self.update(index=1, role_card_code=first["card_code"], api_profile_id=one["profile_id"], ai_enabled=True).status_code, 200)
        self.assertEqual(self.default(role_card_code=second["card_code"], api_profile_id=two["profile_id"]).status_code, 200)
        inherited, explicit = self.preferences(), self.preferences(1)
        self.assertEqual(inherited["effective"]["role_card_code"], second["card_code"])
        self.assertEqual(inherited["effective"]["api_profile_id"], two["profile_id"])
        self.assertEqual(explicit["effective"]["role_card_code"], first["card_code"])
        self.assertEqual(explicit["effective"]["api_profile_id"], one["profile_id"])
        mixed = self.update(1, role_card_code=first["card_code"], memory="keep").json()
        self.assertEqual((mixed["effective"]["role_source"], mixed["effective"]["api_source"]), ("explicit", "default"))
        self.assertEqual(self.update(2, ai_enabled=False).json()["memory"], "keep")
        self.assertEqual(self.preferences(1)["memory"], "")

    def test_enable_missing_rejected_atomically_and_noop_does_not_increment(self):
        events = []; self.store.notify = events.append
        result = self.update(ai_enabled=True, memory="should not commit")
        self.assertEqual((result.status_code, result.json()["code"]), (409, "ai_configuration_missing"))
        self.assertEqual(self.preferences()["revision"], 0)
        self.assertEqual(self.preferences()["memory"], "")
        self.assertEqual(events, [])
        self.create()
        self.assertEqual(self.update(ai_enabled=True).json()["code"], "default_api_profile_missing")
        self.create_api()
        self.assertEqual(self.update(ai_enabled=True).status_code, 200)
        self.assertEqual(self.update(1, ai_enabled=True).json()["revision"], 1)

    def test_explicit_and_inherited_use_blocks_deletion_and_switch_releases(self):
        first, second = self.create(), self.create()
        one, two = self.create_api(), self.create_api()
        self.update(ai_enabled=True)
        self.update(index=1, role_card_code=first["card_code"], api_profile_id=one["profile_id"])
        for path, identity in (("role-cards", {"card_code": first["card_code"]}), ("api-profiles", {"profile_id": one["profile_id"]})):
            self.assertEqual(self.post(path+"/get", **identity).json()["usage_count"], 2)
            self.assertEqual(self.post(path+"/delete", expected_revision=1, **identity).status_code, 409)
        self.default(role_card_code=second["card_code"], api_profile_id=two["profile_id"])
        self.update(1, index=1, role_card_code=None, api_profile_id=None)
        self.assertEqual(self.post("api-profiles/delete", profile_id=one["profile_id"], expected_revision=1).status_code, 200)
        self.assertEqual(self.post("role-cards/delete", card_code=first["card_code"], expected_revision=1).status_code, 200)
        self.assertEqual(self.post("api-profiles/get", profile_id=two["profile_id"]).json()["usage_count"], 1)

    def test_deleting_unused_default_clears_without_promoting_then_new_first_defaults(self):
        first, second = self.create(), self.create()
        one, two = self.create_api(), self.create_api()
        self.post("role-cards/delete", card_code=first["card_code"], expected_revision=1)
        self.post("api-profiles/delete", profile_id=one["profile_id"], expected_revision=1)
        self.assertIsNone(self.post("ai-defaults/query").json()["role_card_code"])
        self.assertIsNone(self.post("ai-defaults/query").json()["api_profile_id"])
        self.assertFalse(self.create_api()["is_default"])
        self.post("role-cards/delete", card_code=second["card_code"], expected_revision=1)
        self.assertTrue(self.create()["is_default"])

    def test_unreadable_key_blocks_enable_and_rebinding_but_allows_memory_and_disable(self):
        self.create(); profile = self.create_api()
        self.update(ai_enabled=True)
        self.store.call(lambda: self.store.db.execute("UPDATE api_profiles SET secret=?", (b"corrupt-ciphertext",)))
        self.store.call(self.store.db.commit)
        self.assertFalse(self.preferences()["effective"]["ready"])
        self.assertFalse(self.store.conversations(self.ids[0])["items"][0]["ai_configuration_ready"])
        self.assertEqual(self.update(1, api_profile_id=profile["profile_id"]).json()["code"], "api_key_unreadable")
        self.assertEqual(self.update(1, memory="repair later").status_code, 200)
        self.assertEqual(self.update(2, ai_enabled=False).status_code, 200)
        self.assertEqual(self.default(api_profile_id=profile["profile_id"]).json()["code"], "api_key_unreadable")
        self.assertEqual(self.edit_api(profile, api_key="replacement-synthetic").status_code, 200)
        self.assertEqual(self.update(3, ai_enabled=True).status_code, 200)

    def test_encryption_failure_rolls_back_profile_and_notifications(self):
        profile = self.create_api()
        # Completed profile visibility precedes its final diagnostic notification.
        for _ in range(200):
            with self.app.state.ai.lock:
                pending = bool(self.app.state.ai.pending)
            if not pending:
                break
            time.sleep(.01)
        self.assertFalse(pending)
        events = []; self.store.notify = events.append
        with patch("backend.app.secret_protection.protect", side_effect=ConversationError("secret_protection_failed", "密钥加密失败", 503)):
            self.assertEqual(self.edit_api(profile, name="lost", api_key="synthetic").status_code, 503)
        self.assertEqual(self.post("api-profiles/get", profile_id=profile["profile_id"]).json()["name"], profile["name"])
        self.assertEqual(events, [])

    def test_default_revision_conflict_and_concurrent_binding_deletion(self):
        one, two = self.create_api(), self.create_api()
        revision = self.post("ai-defaults/query").json()["revision"]
        self.default(api_profile_id=two["profile_id"])
        self.assertEqual(self.post("ai-defaults/update", expected_revision=revision, api_profile_id=one["profile_id"]).json()["code"], "revision_conflict")
        def run(fn):
            try: return fn()
            except ConversationError as exc: return exc.code
        with ThreadPoolExecutor(2) as executor:
            bind = executor.submit(run, lambda: self.store.save_preferences(self.ids[0], self.cids[0], 0, {"api_profile_id": one["profile_id"]}))
            delete = executor.submit(run, lambda: self.store.delete_api_profile(one["profile_id"], 1))
            results = [bind.result(), delete.result()]
        self.assertEqual(sum(isinstance(r, str) for r in results), 1)
        self.assertEqual(self.store.call(lambda: self.store.db.execute("PRAGMA foreign_key_check").fetchall()), [])

    def test_restart_preserves_encrypted_profiles_defaults_and_inheritance(self):
        self.create(); profile = self.create_api()
        self.update(ai_enabled=True, memory="saved")
        self.store.close()
        with closing(Store(self.path)) as reopened:
            value = reopened.preferences(self.ids[0], self.cids[0])
            self.assertTrue(value["effective"]["ready"])
            self.assertEqual(value["memory"], "saved")
            self.assertIsNone(value["api_profile_id"])
            self.assertEqual(value["effective"]["api_profile_id"], profile["profile_id"])

    def test_concurrent_default_updates_have_one_winner(self):
        first, second, third = self.create_api(), self.create_api(), self.create_api()
        revision = self.store.ai_defaults()["revision"]
        def choose(profile):
            try:
                return self.store.save_ai_defaults(revision, {"api_profile_id": profile["profile_id"]})
            except ConversationError as exc:
                return exc.code
        with ThreadPoolExecutor(2) as executor:
            futures = [executor.submit(choose, profile) for profile in (second, third)]
            results = [future.result() for future in futures]
        self.assertEqual(sum(result == "revision_conflict" for result in results), 1)
        listed = self.store.api_profiles()["items"]
        self.assertEqual(sum(profile["is_default"] for profile in listed), 1)
        self.assertNotEqual(self.store.ai_defaults()["api_profile_id"], first["profile_id"])

    def test_v3_upgrade_and_failure_preserve_legacy_enabled_preferences(self):
        first, second = self.create(), self.create()
        # Windows clock resolution can give both inserts the same creation timestamp.
        self.store.call(lambda: self.store.db.execute("UPDATE role_cards SET created_at='2020-01-01T00:00:00+00:00' WHERE card_code=?", (first['card_code'],)))
        self.store.call(self.store.db.commit)
        self.update(role_card_code=second["card_code"], memory="legacy")
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            remove_v5(db)
            db.executescript("DROP TABLE ai_defaults; DROP INDEX preferences_api; ALTER TABLE conversation_preferences DROP COLUMN api_profile_id; DROP TABLE api_profiles; DELETE FROM schema_migrations WHERE version=4; PRAGMA user_version=3; UPDATE conversation_preferences SET ai_enabled=1;")
        with patch("backend.app.storage.AI_SCHEMA", (*AI_SCHEMA, "INVALID SQL")):
            with self.assertRaises(ConversationError): Store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='api_profiles'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT memory FROM conversation_preferences").fetchone()[0], "legacy")
        with closing(Store(self.path)) as reopened:
            self.assertEqual(reopened.ai_defaults()["role_card_code"], first["card_code"])
            value = reopened.preferences(self.ids[0], self.cids[0])
            self.assertTrue(value["ai_enabled"])
            self.assertEqual(value["role_card_code"], second["card_code"])
            self.assertEqual(value["memory"], "legacy")
            self.assertFalse(value["effective"]["ready"])
            self.assertEqual(reopened.role_card(first["card_code"])["card"]["spec"], "chara_card_v2")

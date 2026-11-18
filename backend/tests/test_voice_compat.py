"""v13 rollback preserves audio and fences the entire unsupported reply."""
import json
from unittest.mock import patch

from backend.app.storage import Store
from backend.app.voice_schema import upgrade_v13, VOICE_TABLES
from backend.app.ai_execution_models import AiRun
import test_reply_media as media_tests
from test_roles import RoleTestCase
from migration_helpers import remove_v13


class VoiceCompatibilityTests(RoleTestCase):
    configured = media_tests.ReplyMediaStoreTests.configured
    delta = media_tests.ReplyMediaStoreTests.delta
    start = media_tests.ReplyMediaStoreTests.start
    wait_run = media_tests.ReplyMediaStoreTests.wait_run
    prepare = media_tests.ReplyMediaStoreTests.prepare
    reply = media_tests.ReplyMediaStoreTests.reply
    def migrated_run(self):
        self.prepare()
        run = self.reply('前文\n[[SPLIT]]\n后文')
        def upgrade():
            upgrade_v13(self.store.db)
            with self.store.db:
                self.store.db.execute('INSERT INTO reply_audio VALUES(?,?,?,?,?,?,?,?)',
                    ('audio_test', b'preview-evidence', b'silk-evidence', 1000, 24000, 'a'*64, 'b'*64, 29))
                self.store.db.execute("UPDATE deliveries SET kind='voice',audio_id='audio_test' WHERE run_id=? AND part_index=1", (run['run_id'],))
                value = json.loads(self.store.db.execute('SELECT payload FROM ai_runs WHERE run_id=?', (run['run_id'],)).fetchone()[0])
                value['reply_format_version'] = 3
                value['reply_parts'][1] = dict(index=1, kind='voice', text='后文', audio=dict(audio_id='audio_test',
                    mime_type='audio/wav', size_bytes=16, sha256='a'*64, duration_ms=1000, sample_rate=24000))
                self.store._ai_write_run(value)
        self.store.call(upgrade)
        return run

    def test_voice_feature_creates_v13(self):
        self.assertEqual(self.store.call(lambda: self.store.db.execute('PRAGMA user_version').fetchone()[0]), 13)

    @patch('backend.app.delivery_storage.REPLY_FORMAT_VERSION', 2)
    def test_voice_mixed_run_is_preserved_and_no_prefix_is_sent_on_rollback(self):
        run = self.migrated_run()
        self.assertEqual(self.store.delivery_candidates(), [])
        self.assertIsNone(self.store.claim_delivery(run['run_id'], 0))
        self.store.close()
        reopened = Store(self.path)
        try:
            self.assertEqual(reopened.delivery_candidates(), [])
            value = reopened.ai_run(self.ids[0], self.cids[0], run['run_id'])
            self.assertEqual(AiRun.model_validate(value).reply_parts[1].audio.duration_ms, 1000)
            self.assertEqual(value['delivery']['parts'][1]['kind'], 'voice')
            self.assertEqual(reopened.call(lambda: tuple(reopened.db.execute('SELECT wav,silk FROM reply_audio').fetchone())),
                             (b'preview-evidence', b'silk-evidence'))
            self.assertEqual(reopened.call(lambda: reopened.db.execute('PRAGMA foreign_key_check').fetchall()), [])
        finally:
            reopened.close()

    @patch('backend.app.delivery_storage.REPLY_FORMAT_VERSION', 2)
    def test_voice_preparation_is_paused_without_losing_generated_result(self):
        run = self.migrated_run()
        def pending():
            with self.store.db:
                self.store.db.execute('DELETE FROM deliveries WHERE run_id=?', (run['run_id'],))
                value = json.loads(self.store.db.execute('SELECT payload FROM ai_runs WHERE run_id=?', (run['run_id'],)).fetchone()[0])
                value['status'] = 'running'
                self.store._ai_write_run(value)
                self.store.db.execute('INSERT INTO voice_preparations VALUES(?,?)', (run['run_id'], '{"result":"preserve-generated-result"}'))
                self.store._ai_recover()
        self.store.call(pending)
        value = self.store.ai_run(self.ids[0], self.cids[0], run['run_id'])
        self.assertEqual((value['status'], value['error']), ('queued', 'reply_version_unsupported'))
        self.assertEqual(self.store.ai_retry_candidates(), [])
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT payload FROM voice_preparations').fetchone()[0]),
                         '{"result":"preserve-generated-result"}')

    def test_voice_migration_failure_rolls_back_schema_and_keeps_foreign_keys(self):
        self.prepare()
        run = self.reply('保留的回复')
        self.store.call(remove_v13, self.store.db)
        with patch('backend.app.voice_schema.VOICE_TABLES', (*VOICE_TABLES, 'INVALID SQL')):
            with self.assertRaises(Exception):
                self.store.call(upgrade_v13, self.store.db)
        def inspect():
            return (self.store.db.execute('PRAGMA user_version').fetchone()[0],
                    self.store.db.execute('PRAGMA foreign_keys').fetchone()[0],
                    self.store.db.execute("SELECT count(*) FROM sqlite_master WHERE name='reply_audio'").fetchone()[0])
        self.assertEqual(self.store.call(inspect), (12, 1, 0))
        self.assertEqual(self.store.call(lambda: self.store.db.execute('SELECT run_id FROM deliveries').fetchone()[0]), run['run_id'])

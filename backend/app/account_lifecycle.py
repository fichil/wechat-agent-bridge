"""Transactional removal and fences against callbacks from an old account ID."""
from .conversations import ConversationError
from .conversation_settings import ConversationSettingsStore
from .role_storage import conflict


class AccountLifecycleMixin:
    account_generation = 0

    def delete_account(self, aid, revision):
        def write():
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                account = self._account(aid)
                if account['revision'] != revision: conflict()
                if account['source_state'] != 'offline':
                    raise ConversationError('account_not_offline', '只有确认离线的账号可以删除，请刷新账号状态。', 409)
                owned = {aid, *(r[0] for r in self.db.execute('SELECT conversation_id FROM conversations WHERE account_id=?',(aid,)))}
                # Child rows precede parents; foreign keys remain enabled throughout.
                self.db.execute('DELETE FROM delivery_attempts WHERE run_id IN (SELECT run_id FROM ai_runs WHERE account_id=?)', (aid,))
                self.db.execute('DELETE FROM deliveries WHERE account_id=?', (aid,))
                self.db.execute('DELETE FROM group_round_messages WHERE round_id IN (SELECT round_id FROM group_rounds WHERE account_id=?)', (aid,))
                for table in ('group_rounds','group_message_eligibility','reply_triggers','delivery_sessions','ai_runs','ai_sessions',
                              'conversation_preferences','history_ranges','messages','media_cache','sync_state','sync_jobs',
                              'cursors','diagnostic_logs','notification_outbox','account_observations','conversations'):
                    self.db.execute('DELETE FROM '+table+' WHERE account_id=?', (aid,))
                self.db.execute('DELETE FROM conversation_settings WHERE account_key=?', (ConversationSettingsStore.key(account['wxid']),))
                self.db.execute('DELETE FROM compatibility_state WHERE scope=?', (aid,))
                # Metadata keys use an exact account component, never a nickname/wxid match.
                for row in self.db.execute('SELECT key FROM metadata').fetchall():
                    if owned.intersection(row[0].split(':')): self.db.execute('DELETE FROM metadata WHERE key=?', (row[0],))
                self.db.execute('DELETE FROM accounts WHERE account_id=?', (aid,))
                self._media_collect()
            self.account_generation += 1
            self.source_versions = {k:v for k,v in self.source_versions.items() if k[0] != aid}
            self.notify(dict(type='changed', account_id=aid, resources=['accounts','account_deleted']))
            self.notify(dict(type='changed', resources=['role_cards','api_profiles','notification_settings']))
            return dict(account_id=aid, deleted=True)
        return self.call(write)

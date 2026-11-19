"""Independent speech configuration; protected credentials never enter role cards."""
import base64
import json

from . import secret_protection
from .conversations import ConversationError
from .role_storage import conflict
from .voice_models import VoiceServiceBinding, VoiceServiceSettings

SERVICE_KEY = 'speech-service:bailian'


def service_for_card(card):
    extension = card.get('data', {}).get('extensions', {}).get('wechat_agent_bridge', {})
    value = extension.get('voice_service') if isinstance(extension, dict) else None
    return VoiceServiceBinding.model_validate(value).model_dump() if value is not None else None


def service_profile(workspace_id):
    return dict(profile_id='speech-service:bailian', speech_protocol='dashscope_cosyvoice',
                base_url=f'https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1',
                model='cosyvoice-v3-flash', speech_model='cosyvoice-v3-flash')


class VoiceServiceStoreMixin:
    def _voice_service(self):
        row = self.db.execute('SELECT value FROM metadata WHERE key=?', (SERVICE_KEY,)).fetchone()
        return json.loads(row[0]) if row else dict(revision=0, workspace_id='', verification={})

    def voice_service(self):
        def read():
            value = self._voice_service()
            secret = base64.b64decode(value.get('protected_key', ''))
            return VoiceServiceSettings(revision=value['revision'], workspace_id=value['workspace_id'],
                has_key=bool(secret), key_readable=bool(secret) and secret_protection.verify(secret),
                verification=value.get('verification', {})).model_dump()
        return self.call(read)

    def voice_service_credentials(self, revision):
        def read():
            value = self._voice_service()
            if value['revision'] != revision: conflict()
            secret = base64.b64decode(value.get('protected_key', ''))
            if not secret:
                raise ConversationError('voice_service_unconfigured', '请先填写并测试百炼语音配置。', 409)
            return service_profile(value['workspace_id']), secret
        return self.call(read)

    def save_voice_service(self, revision, workspace_id, protected_key, verification):
        def write():
            with self.db:
                if self._voice_service()['revision'] != revision: conflict()
                value = dict(revision=revision+1, workspace_id=workspace_id,
                    protected_key=base64.b64encode(protected_key).decode('ascii'), verification=verification)
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',
                                (SERVICE_KEY, json.dumps(value, ensure_ascii=False)))
            self.notify(dict(type='changed', resources=['voice_service']))
        self.call(write)
        return self.voice_service()

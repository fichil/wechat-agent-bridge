import base64
from copy import deepcopy
import json
from unittest.mock import patch

from backend.app.conversations import ConversationError
from backend.app.role_evolution import merge_role_patch
from backend.app.voice_service import SERVICE_KEY
from bridge_portable.migrate import quarantine
from test_roles import RoleTestCase, card
import test_voice_replies as voices


STYLE = '你说话的角色是傲娇公主，你说话的情感是neutral。'
BINDING = dict(service_id='bailian', voice='longhuhu_v3', instructions=STYLE)


class IndependentVoiceTests(RoleTestCase):
    configured = voices.VoiceReplyTests.configured
    delta = voices.VoiceReplyTests.delta
    start = voices.VoiceReplyTests.start
    wait_run = voices.VoiceReplyTests.wait_run
    prepare = voices.VoiceReplyTests.prepare
    reply = voices.VoiceReplyTests.reply

    def setUp(self):
        super().setUp()
        self.speech = voices.FakeSpeech()
        self.app.state.ai.speech.provider = self.speech

    def configure(self, revision=0, **fields):
        response = self.post('voice/service/update', expected_revision=revision,
            workspace_id='synthetic-workspace', **dict(api_key='independent-test-secret', **fields))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def bind(self):
        role = self.store.role_card(self.preferences()['effective']['role_card_code'])
        role['card']['data']['extensions']['wechat_agent_bridge'] = {
            'voices': {self.profile['profile_id']: 'cedar'}, 'voice_service': deepcopy(BINDING)}
        return self.store.save_role_card(role['card'],role['card_code'],role['revision'])

    def test_configure_is_independent_encrypted_and_keyless_readback(self):
        self.prepare()
        before = self.store.api_profiles()
        result = self.configure()
        self.assertEqual((result['revision'],result['has_key'],result['key_readable']),(1,True,True))
        self.assertEqual(self.store.api_profiles(),before)
        raw = self.store.call(lambda:self.store.db.execute('SELECT value FROM metadata WHERE key=?',(SERVICE_KEY,)).fetchone()[0])
        self.assertNotIn('independent-test-secret',raw)
        blob = base64.b64decode(json.loads(raw)['protected_key'])
        self.assertNotIn(b'independent-test-secret',blob)
        readback = self.post('voice/service/query').text
        for forbidden in ('independent-test-secret','protected_key','api_key'):
            self.assertNotIn(forbidden,readback)
        with patch.object(self.speech,'test',wraps=self.speech.test) as test:
            keep = self.post('voice/service/update', expected_revision=1, workspace_id='synthetic-workspace', api_key='')
        self.assertEqual(keep.json()['revision'],2)
        self.assertEqual(test.call_args.args[1],'independent-test-secret')

    def test_failed_test_stale_update_and_invalid_workspace_do_not_replace_config(self):
        before = self.configure()
        with patch.object(self.speech,'test',return_value=dict(status='failed',message='secret provider body')):
            response = self.post('voice/service/update',expected_revision=1,workspace_id='new-workspace',api_key='new-test-secret')
        self.assertEqual(response.status_code,422)
        self.assertNotIn('secret provider body',response.text)
        self.assertEqual(self.store.voice_service(),before)
        with patch.object(self.speech,'test') as test:
            response = self.post('voice/service/update',expected_revision=0,workspace_id='new-workspace',api_key='new-test-secret')
            invalid = self.post('voice/service/update',expected_revision=1,workspace_id='evil.test/path',api_key='new-test-secret')
        self.assertEqual(response.json()['code'],'revision_conflict')
        self.assertEqual(invalid.status_code,422)
        test.assert_not_called()

    def test_concurrent_update_discards_tested_but_stale_configuration(self):
        self.configure()
        def concurrent(_profile, _key):
            _, secret = self.store.voice_service_credentials(1)
            self.store.save_voice_service(1,'winner-workspace',secret,dict(status='supported'))
            return dict(status='supported')
        with patch.object(self.speech,'test',side_effect=concurrent):
            response = self.post('voice/service/update',expected_revision=1,workspace_id='loser-workspace',api_key='new-test-secret')
        self.assertEqual(response.json()['code'],'revision_conflict')
        self.assertEqual(self.store.voice_service()['workspace_id'],'winner-workspace')

    def test_accepted_voice_works_without_chat_speech_and_is_snapshotted(self):
        self.prepare(); self.configure(); self.bind()
        self.store.save_speech_verification(self.profile['profile_id'],1,dict(status='unsupported'))
        snapshots=[]
        read=self.store.voice_request_snapshot
        def capture(*args):
            result=read(*args)
            snapshots.append(deepcopy(result[1]))
            return result
        with patch.object(self.store,'voice_request_snapshot',side_effect=capture):
            run = self.reply('[[VOICE]]\n才不是大肥鱼，是小鲸鱼。')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['reply_parts'][0]['kind'],'voice')
        self.assertEqual(self.speech.calls,[('speech-service:bailian','independent-test-secret','才不是大肥鱼，是小鲸鱼。','longhuhu_v3')])
        self.assertEqual(self.speech.options[0]['instructions'],STYLE)
        snapshot = snapshots[0]
        self.assertEqual(snapshot['envelope']['reply_media']['voice']['service_revision'],1)
        self.assertNotIn('independent-test-secret',json.dumps(snapshot))

    def test_service_voice_survives_chat_fallback(self):
        self.prepare(); self.configure(); self.bind()
        backup = self.create_api(name='备用',api_key='backup-only-key')
        seen = []
        def generate(profile,key,envelope,mode):
            seen.append(profile['profile_id'])
            if profile['profile_id']==self.profile['profile_id']:
                error=ConversationError('ai_provider_error','当前 Agent 不可用',503); error.retryable=False
                raise error
            self.assertTrue(envelope['reply_media']['capabilities']['voice'])
            return dict(memory='新记忆',reply='[[VOICE]]\n仍是小鲸鱼')
        with patch.object(self.provider,'generate',side_effect=generate):
            run=self.store.start_ai_run(self.ids[0],self.cids[0],'voice-service-fallback','manual')
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
            self.app.state.ai.perform(self.ids[0],self.cids[0],run['run_id'])
            run=self.wait_run(run)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(seen,[self.profile['profile_id'],backup['profile_id']])
        self.assertEqual(self.speech.calls,[('speech-service:bailian','independent-test-secret','仍是小鲸鱼','longhuhu_v3')])

    def test_missing_or_unreadable_service_falls_back_without_using_mature_voice(self):
        self.prepare(); self.bind()
        run = self.reply('[[VOICE]]\n未配置时保留原文')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['reply_parts'][0]['kind'],'text')
        self.assertEqual(self.speech.calls,[])

    def test_changed_service_during_synthesis_falls_back_to_original_text(self):
        self.prepare(); self.configure(); self.bind()
        def revise():
            _, secret = self.store.voice_service_credentials(1)
            self.store.save_voice_service(1,'another-workspace',secret,dict(status='supported'))
        self.speech.hook=revise
        run=self.reply('[[VOICE]]\n保留全部原文')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual((run['reply_parts'][0]['kind'],run['reply_parts'][0]['text']),('text','保留全部原文'))
        self.assertEqual(len(self.speech.calls),1)

    def test_credential_failure_after_snapshot_falls_back_instead_of_failing_run(self):
        self.prepare(); self.configure(); self.bind()
        with patch.object(self.store,'voice_service_credentials',side_effect=ConversationError('api_key_unreadable','无法读取',409)):
            run=self.reply('[[VOICE]]\n原文')
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['reply_parts'][0]['kind'],'text')
        self.assertEqual(self.speech.calls,[])

    def test_preview_is_separate_from_chat_and_discards_stale_audio(self):
        self.configure()
        response = self.post('voice/service/preview',expected_revision=1,text='我听着。',**BINDING)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.speech.calls[-1],('speech-service:bailian','independent-test-secret','我听着。','longhuhu_v3'))
        self.assertEqual(self.store.call(lambda:self.store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]),0)
        def revise():
            _, secret = self.store.voice_service_credentials(1)
            self.store.save_voice_service(1,'another-workspace',secret,dict(status='supported'))
        self.speech.hook=revise
        stale=self.post('voice/service/preview',expected_revision=1,**BINDING)
        self.assertEqual(stale.json()['code'],'revision_conflict')
        self.assertNotIn('data_base64',stale.text)

    def test_role_override_roundtrip_evolution_and_validation(self):
        document=card()
        document['data']['extensions']['wechat_agent_bridge']={'voice_service':BINDING,'future':True}
        saved=self.create(document)
        self.assertEqual(self.create(saved['card'])['card'],document)
        changed,_=merge_role_patch(document,json.dumps({'personality':'更温柔'}),True)
        self.assertEqual(changed['data']['extensions'],document['data']['extensions'])
        for binding in (None, [], {}, dict(BINDING,service_id='unknown'),dict(BINDING,api_key='forbidden'),
                        dict(BINDING,instructions='童'*51),dict(BINDING,voice='bad\nvoice')):
            document['data']['extensions']['wechat_agent_bridge']['voice_service']=binding
            response=self.post('role-cards/create',card=document)
            self.assertEqual((response.status_code,response.json()['code']),(422,'voice_service_invalid'))

    def test_migration_clears_foreign_service_but_update_quarantine_preserves_it(self):
        before=self.configure()
        self.store.call(lambda:quarantine(self.store.db))
        self.assertEqual(self.store.voice_service(),before)
        self.store.call(lambda:quarantine(self.store.db,reset_compatibility=True))
        self.assertFalse(self.store.voice_service()['has_key'])
        self.assertEqual(self.store.voice_service()['verification']['status'],'unverified')

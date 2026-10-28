"""Only synthetic protocol dictionaries; no Tencent, OpenAI or client access."""
import copy
import unittest
from wechat_agent_bridge.adapters.clawbot_reference import ReferenceSession, accepted_response
from wechat_agent_bridge.console_models import ConsoleError


class ClawbotReferenceTests(unittest.TestCase):
    def fixture(self, bot='synthetic-bot', token='synthetic-context'):
        return {'ret':0,'get_updates_buf':'synthetic-cursor','msgs':[{
            'message_type':1,'message_state':2,'from_user_id':'synthetic@im.wechat',
            'to_user_id':bot,'context_token':token,'item_list':[{'type':1,'text_item':{'text':'合成消息'}}]}]}

    def test_account_context_isolation_and_no_personal_id_alias(self):
        s=ReferenceSession()
        for bot in ('bot-a','bot-b'):s.consume_updates(bot,'synthetic@im.wechat',self.fixture(bot,bot+'-token'))
        a=s.reply_envelope('bot-a','synthetic@im.wechat','合成回复','request-a')
        self.assertEqual(a['msg']['context_token'],'bot-a-token')
        for bot,peer in (('bot-c','synthetic@im.wechat'),('bot-a','wxid_synthetic')):
            with self.assertRaises(ConsoleError):s.reply_envelope(bot,peer,'合成回复','request-b')

    def test_group_field_does_not_mean_group_support(self):
        s=ReferenceSession();data=self.fixture();data['msgs'][0]['group_id']='synthetic-group'
        with self.assertRaisesRegex(ConsoleError,'group_unsupported'):s.consume_updates('synthetic-bot','synthetic@im.wechat',data)
        self.assertFalse(s.cursors)

    def test_failed_batch_does_not_advance_cursor_or_context(self):
        s=ReferenceSession();data=self.fixture()
        bad=copy.deepcopy(data['msgs'][0]);bad['to_user_id']='another-bot';data['msgs'].append(bad)
        with self.assertRaises(ConsoleError):s.consume_updates('synthetic-bot','synthetic@im.wechat',data)
        self.assertFalse(s.cursors);self.assertFalse(s.contexts)

    def test_own_message_does_not_trigger_or_create_context(self):
        s=ReferenceSession();data=self.fixture();data['msgs'][0]['message_type']=2
        self.assertEqual(s.consume_updates('synthetic-bot','synthetic@im.wechat',data),[])
        self.assertFalse(s.contexts)

    def test_media_is_unprocessed_not_fabricated(self):
        s=ReferenceSession();data=self.fixture();data['msgs'][0]['item_list']=[{'type':2,'image_item':{'media':{'full_url':'https://example.invalid/private'}}}]
        event=s.consume_updates('synthetic-bot','synthetic@im.wechat',data)[0]
        self.assertEqual(event['text'],'');self.assertEqual(event['unprocessed_item_types'],[2])

    def test_backend_acceptance_is_not_delivery(self):
        self.assertEqual(accepted_response({'ret':0}),'backend_accepted_unverified_delivery')
        for response in ({},{'ret':False},{'ret':-1},{'ret':0,'errcode':-14}):
            with self.assertRaises(ConsoleError):accepted_response(response)

    def test_missing_context_cannot_generate_reply(self):
        s=ReferenceSession();data=self.fixture();del data['msgs'][0]['context_token']
        with self.assertRaises(ConsoleError):s.consume_updates('synthetic-bot','synthetic@im.wechat',data)
        with self.assertRaises(ConsoleError):s.reply_envelope('synthetic-bot','synthetic@im.wechat','合成回复','r')

    def test_malformed_items_cannot_partially_update_session(self):
        for item in (None,'untrusted',{'type':1,'text_item':[]}):
            s=ReferenceSession();data=self.fixture();data['msgs'][0]['item_list']=[item]
            with self.subTest(item=item),self.assertRaises(ConsoleError):
                s.consume_updates('synthetic-bot','synthetic@im.wechat',data)
            self.assertFalse(s.cursors);self.assertFalse(s.contexts)

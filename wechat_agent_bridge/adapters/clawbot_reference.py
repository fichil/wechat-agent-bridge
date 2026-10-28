"""Offline contract experiment, NOT a registered or live-capable adapter.

Independent implementation against Tencent's published protocol at commit
874637a0dbc24050f72d1385add4fc6170a2d726. No HTTP, login, credential or media I/O.
Our stricter rejection rules are project policy, not claimed server requirements.
"""
from ..console_models import ConsoleError

REFERENCE_COMMIT = '874637a0dbc24050f72d1385add4fc6170a2d726'


def accepted_response(response):
    if (not isinstance(response,dict) or type(response.get('ret')) is not int
            or response['ret'] != 0 or response.get('errcode',0) != 0):
        raise ConsoleError('clawbot_reference_response_unverified')
    return 'backend_accepted_unverified_delivery'


class ReferenceSession:
    def __init__(self):
        self.cursors = {}
        self.contexts = {}

    def consume_updates(self, bot_id, peer_id, response):
        accepted_response(response)
        if (not isinstance(bot_id,str) or not bot_id or not isinstance(peer_id,str)
                or not peer_id.endswith('@im.wechat')):
            raise ConsoleError('clawbot_reference_scope_required')
        cursor, messages = response.get('get_updates_buf'),response.get('msgs')
        if not isinstance(cursor,str) or not cursor or not isinstance(messages,list):
            raise ConsoleError('clawbot_reference_updates_incomplete')
        events, contexts = [], {}
        for message in messages:
            if not isinstance(message,dict) or message.get('group_id'):
                raise ConsoleError('clawbot_reference_group_unsupported')
            if type(message.get('message_type')) is int and message['message_type'] == 2: continue
            if (type(message.get('message_type')) is not int or message.get('message_type') != 1 or message.get('message_state') != 2
                    or message.get('from_user_id') != peer_id or message.get('to_user_id') != bot_id):
                raise ConsoleError('clawbot_reference_target_or_state_mismatch')
            token, items = message.get('context_token'),message.get('item_list')
            if not isinstance(token,str) or not token or not isinstance(items,list):
                raise ConsoleError('clawbot_reference_context_incomplete')
            text, unavailable = [], []
            for item in items:
                if not isinstance(item,dict): raise ConsoleError('clawbot_reference_item_invalid')
                if item.get('type') == 1:
                    body=item.get('text_item')
                    value = body.get('text') if isinstance(body,dict) else None
                    if not isinstance(value,str): raise ConsoleError('clawbot_reference_text_invalid')
                    text.append(value)
                else: unavailable.append(item.get('type'))
            key = ('clawbot',bot_id,peer_id)
            contexts[key] = token
            events.append({'scope':key,'text':'\n'.join(text),'unprocessed_item_types':unavailable})
        # No partial cursor/token updates if another message fails validation.
        self.contexts.update(contexts)
        self.cursors[bot_id] = cursor
        return events

    def reply_envelope(self, bot_id, peer_id, text, client_id):
        token = self.contexts.get(('clawbot',bot_id,peer_id))
        if not token: raise ConsoleError('clawbot_reference_context_missing')
        if not isinstance(text,str) or not text or len(text)>500 or not isinstance(client_id,str) or not client_id:
            raise ConsoleError('clawbot_reference_reply_invalid')
        return {'msg':{'from_user_id':'','to_user_id':peer_id,'client_id':client_id,
                       'message_type':2,'message_state':2,'context_token':token,
                       'item_list':[{'type':1,'text_item':{'text':text}}]}}

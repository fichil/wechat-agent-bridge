"""Bound, read-only loaded-text adapter shared by console and acceptance.

Runtime UI readers are injected; importing this module never touches Windows.
Unknown sender, mention and media semantics remain explicitly unknown.
"""
import time
import json

from ..console_models import ChatMessage, MessagePage, ConsoleError
from ..console_store import digest


def check_read_target(expected, current):
    """Identity-only check: editing a draft is not a read conflict.

    No draft text crosses this boundary. Do not use this to qualify a write.
    """
    if current.get('identity_verified') is not True or current.get('target_index') != 0:
        raise ConsoleError('bound_console_group_changed')
    for key in ('session_runtime_id', 'input_runtime_id', 'group_header_runtime_id'):
        if not expected.get(key) or current.get(key) != expected[key]:
            raise ConsoleError('bound_console_group_changed')


def message_page(value,account,chat,hwnd):
    rows=value.get('rows')
    if not isinstance(rows,list) or not 1<=len(rows)<=50:
        raise ConsoleError('group_memory_row_limit')
    seen=set(); messages=[]; skipped=0; size=0
    for row in rows:
        rid=row.get('runtime_id'); text=row.get('label'); cls=row.get('class_name')
        if (not isinstance(rid,list) or len(rid)!=4 or any(type(x) is not int for x in rid) or
            rid[:3]!=[42,hwnd,4] or not -2**31<=rid[3]<-1 or tuple(rid) in seen or
            not isinstance(text,str) or len(text)>32768):
            raise ConsoleError('group_memory_message_identity_invalid')
        seen.add(tuple(rid)); size+=len(text)
        if size>131072: raise ConsoleError('group_memory_text_limit')
        if cls!='mmui::ChatTextItemView':
            skipped+=1; continue  # No guessed time/file/voice body from generic rows.
        messages.append(ChatMessage(account.id,chat.id,digest(str(rid),text),
            '未核验',text,'',own=None,historical=True,mentions_self=None))
    if not messages: raise ConsoleError('group_memory_no_verified_text_rows')
    return MessagePage(tuple(messages),complete=False,
        limitation='loaded_text_only; sender_mentions_time_media_paging_unverified; skipped_rows='+str(skipped))


class GroupReadAdapter:
    simulation_only=False
    def __init__(self,account,chat,hwnd,reader,guard):
        self.account,self.chat,self.hwnd=account,chat,hwnd
        self.reader,self.guard=reader,guard
        self.last_capture=None
        self.validate_page=None
    def _scope(self,account,chat=None):
        if account!=self.account.id or (chat is not None and chat!=self.chat.id):
            raise ConsoleError('group_memory_target_mismatch')
        self.guard()
    def discover(self): self.guard(); return [self.account]
    def conversations(self,account): self._scope(account); return [self.chat]
    def inspect(self,account,chat):
        self._scope(account,chat)
        return dict(account_id=account,conversation_id=chat,identity_certain=True,
                    connected=True,user_active=False,has_draft=False)
    def messages(self,account,chat,cursor=None):
        self._scope(account,chat)
        if cursor is not None: raise ConsoleError('group_memory_paging_unverified')
        value=self.reader(); self.guard()
        page=message_page(value,self.account,self.chat,self.hwnd)
        if self.validate_page: self.validate_page(page)
        self.last_capture=time.monotonic()
        return page
    def attachment(self,*args): raise ConsoleError('group_memory_attachment_unverified')
    def send(self,*args): raise ConsoleError('acceptance_read_only_scope')
    def close(self): pass



def collect_labels(raw, root, uia, pid, cursor=None):
    condition = raw.CreatePropertyCondition(uia.UIA_NamePropertyId, '消息')
    for label in ('消息列表', 'Messages'):
        condition = raw.CreateOrCondition(condition, raw.CreatePropertyCondition(uia.UIA_NamePropertyId, label))
    condition = raw.CreateAndCondition(condition,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListControlTypeId))
    matches = root.FindAll(uia.TreeScope_Descendants, condition)
    if matches.Length != 1:
        raise ConsoleError('message_list_ambiguous')
    listing = matches.GetElement(0)
    listing_id = tuple(listing.GetRuntimeId())
    if listing.CurrentProcessId != pid:
        raise ConsoleError('message_process_mismatch')
    items = listing.FindAll(uia.TreeScope_Children,
        raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, uia.UIA_ListItemControlTypeId))
    if type(items.Length) is not int or not 0 <= items.Length <= 512:
        raise ConsoleError('message_row_limit')
    end = items.Length
    if cursor is not None:
        try:
            anchor = json.loads(cursor)
            if anchor['list'] != list(listing_id):
                raise ValueError()
            indices = [i for i in range(items.Length)
                       if list(items.GetElement(i).GetRuntimeId()) == anchor['row']]
            if len(indices) != 1:
                raise ValueError()
            end = indices[0]
            if digest(items.GetElement(end).CurrentName) != anchor['digest']:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise ConsoleError('history_cursor_changed') from None
    start = max(0, end - 50)
    rows, seen, total = [], set(), 0
    for index in range(start, end):
        item = items.GetElement(index)
        runtime = tuple(item.GetRuntimeId())
        if not runtime or runtime in seen or item.CurrentProcessId != pid:
            raise ConsoleError('message_identity_changed')
        seen.add(runtime)
        label = item.CurrentName
        if not isinstance(label, str) or len(label) > 32768:
            raise ConsoleError('message_label_limit')
        total += len(label)
        if total > 131072:
            raise ConsoleError('message_total_limit')
        rows.append(dict(runtime_id=list(runtime), label=label, class_name=item.CurrentClassName))
        if tuple(item.GetRuntimeId()) != runtime:
            raise ConsoleError('message_identity_changed')
    if listing.CurrentProcessId != pid or tuple(listing.GetRuntimeId()) != listing_id:
        raise ConsoleError('message_list_changed')
    next_cursor = json.dumps(dict(list=list(listing_id), row=rows[0]['runtime_id'],
                                  digest=digest(rows[0]['label']))) if start and rows else None
    return dict(list_runtime_id=list(listing_id), loaded_count=items.Length, rows=rows,
                loaded_start=start, loaded_end=end, cursor=next_cursor,
                coverage='accessible_loaded_page_only', historical=True)


def console_message_page(value, account, chat, hwnd):
    """Preserve every accessible row, including opaque media/system labels.

    Only the text class already verified on this build is treated as content.
    Label-only rows never enter model context or trigger a reply. Sender fields
    stay empty: message text and display labels cannot establish a contact ID.
    The older fixed acceptance collector above remains unchanged.
    """
    rows = value.get('rows')
    if not isinstance(rows, list) or len(rows) > 50:
        raise ConsoleError('group_memory_row_limit')
    seen, messages, size = set(), [], 0
    for row in rows:
        rid, label = row.get('runtime_id'), row.get('label')
        if (not isinstance(rid, list) or len(rid) != 4 or
                any(type(x) is not int for x in rid) or rid[:3] != [42, hwnd, 4] or
                not -2**31 <= rid[3] < -1 or tuple(rid) in seen or
                not isinstance(label, str) or len(label) > 32768):
            raise ConsoleError('group_memory_message_identity_invalid')
        seen.add(tuple(rid)); size += len(label)
        if size > 131072:
            raise ConsoleError('group_memory_text_limit')
        text = row.get('class_name') == 'mmui::ChatTextItemView'
        messages.append(ChatMessage(account.id, chat.id, digest(str(rid), label),
            '未核验', label, '', own=None, historical=True, mentions_self=None,
            kind='text' if text else 'unparsed', upload_eligible=text,
            content_status='available' if text else 'client_label_only'))
    return MessagePage(tuple(messages), cursor=value.get('cursor'), complete=False,
        limitation='loaded_rows_only; sender_profile_and_older_history_unavailable',
        loaded_count=value.get('loaded_count'), loaded_start=value.get('loaded_start'),
        loaded_end=value.get('loaded_end'))

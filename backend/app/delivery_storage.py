"""Durable trigger eligibility and an at-most-one-attempt outbox per reply part."""
from datetime import datetime, timezone
import json
import time
import unicodedata
from uuid import uuid4
from .conversations import ConversationError

DELIVERY_SCHEMA = (
    "CREATE TABLE delivery_sessions(account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, enabled_at TEXT NOT NULL, watermarks TEXT NOT NULL, last_trigger_at REAL NOT NULL DEFAULT 0, PRIMARY KEY(account_id,conversation_id), FOREIGN KEY(account_id,conversation_id) REFERENCES conversations(account_id,conversation_id))",
    "CREATE TABLE reply_triggers(message_id TEXT PRIMARY KEY REFERENCES messages, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, epoch INTEGER NOT NULL, consumed_run_id TEXT)",
    "CREATE TABLE deliveries(run_id TEXT NOT NULL REFERENCES ai_runs, part_index INTEGER NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, epoch INTEGER NOT NULL, target TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, code TEXT, confirmation TEXT, archive_message_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(run_id,part_index))",
    "CREATE INDEX delivery_queue ON deliveries(status,account_id,conversation_id)",
    "CREATE TABLE delivery_attempts(attempt_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,part_index INTEGER NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,code TEXT,FOREIGN KEY(run_id,part_index) REFERENCES deliveries(run_id,part_index))",
)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def text_units(text):
    """Keep combining marks, emoji modifiers/ZWJ chains and flag pairs together."""
    unit=''; regional=0
    for ch in text:
        cp=ord(ch)
        is_regional=0x1F1E6<=cp<=0x1F1FF
        extend=(unicodedata.category(ch).startswith('M') or cp==0x200D or
                0x1F3FB<=cp<=0x1F3FF or 0xE0020<=cp<=0xE007F)
        joined=unit and (extend or unit.endswith('\u200d') or (is_regional and regional==1) or (unit=='\r' and ch=='\n'))
        if unit and not joined:
            yield unit
            unit=''; regional=0
        unit+=ch
        regional=regional+1 if is_regional else 0
    if unit: yield unit


def split_reply(text, limit=1000):
    if '\0' in text:
        raise ConversationError('reply_invalid', '回复包含无法发送的 NUL 字符。', 422)
    parts=[]; part=''; length=0
    for ch in text_units(text):
        width=len(ch.encode('utf-8'))
        if width>limit:
            raise ConversationError('reply_invalid', '回复包含超过单段限制的连续组合字符。', 422)
        if length+width>limit:
            parts.append(part); part=''; length=0
        part+=ch; length+=width
    if part: parts.append(part)
    return parts


class DeliveryStoreMixin:
    def _delivery_enable(self, aid, cid):
        watermarks={r['shard']+'|'+r['table_name']:dict(generation=r['generation'],
                    upper=max(r['last_id'],r['upper_id'] or 0)) for r in self.db.execute(
            'SELECT * FROM sync_state WHERE account_id=? AND source_id=(SELECT source_id FROM conversations WHERE conversation_id=?)',(aid,cid))}
        self.db.execute('INSERT OR REPLACE INTO delivery_sessions VALUES(?,?,?,?,0)',(aid,cid,stamp(),json.dumps(watermarks)))

    def _delivery_recover(self):
        self.db.execute("UPDATE deliveries SET status='unknown',code='send_interrupted' WHERE status='sending'")
        self.db.execute("UPDATE delivery_attempts SET status='unknown',code='send_interrupted',completed_at=? WHERE status='sending'",(stamp(),))

    def _delivery_preference_change(self, previous, current):
        aid,cid=current['account_id'],current['conversation_id']
        if current['ai_enabled'] and not previous['ai_enabled']:
            self._delivery_enable(aid,cid)
        if previous['ai_enabled'] and not current['ai_enabled']:
            self.db.execute("UPDATE deliveries SET status='cancelled',code='ai_disabled' WHERE account_id=? AND conversation_id=? AND status IN ('queued','failed')",(aid,cid))

    def _delivery_note_message(self, aid, cid, item, ref, incremental):
        if item.get('is_self') is True:
            # Corroboration only; this never resolves an ambiguous native call.
            if self.db.execute('SELECT 1 FROM deliveries WHERE archive_message_id=?',(item['message_id'],)).fetchone(): return
            candidates=self.db.execute("SELECT d.run_id,d.part_index FROM deliveries d WHERE d.account_id=? AND d.conversation_id=? AND d.text=? AND d.archive_message_id IS NULL AND d.status IN ('sent','sending','unknown') AND EXISTS(SELECT 1 FROM delivery_attempts a WHERE a.run_id=d.run_id AND a.part_index=d.part_index AND unixepoch(a.started_at)<=unixepoch(?))",(aid,cid,item.get('content',{}).get('text'),item.get('sent_at') or '')).fetchall()
            if len(candidates)==1:
                self.db.execute('UPDATE deliveries SET archive_message_id=? WHERE run_id=? AND part_index=?',(item['message_id'],*candidates[0]))
            return
        if not incremental or item.get('is_self') is not False or item.get('type') not in ('text','image','sticker','voice'):
            return
        prefs=self._preferences(aid,cid)
        if not prefs['ai_enabled']: return
        session=self.db.execute('SELECT * FROM delivery_sessions WHERE account_id=? AND conversation_id=?',(aid,cid)).fetchone()
        if not session or not item.get('sent_at'): return
        try:
            if datetime.fromisoformat(item['sent_at'].replace('Z','+00:00')) < datetime.fromisoformat(session['enabled_at']): return
        except ValueError: return
        conv=self._conversation(aid,cid); source=conv['source_id']
        if source.endswith('@chatroom'):
            if (item.get('mention_status')!='known' or item.get('mentions_all') or
                    self._account(aid)['wxid'] not in item.get('mentioned_wxids',[])): return
        elif conv['category'] not in ('contact','enterprise_contacts'):
            return
        mark=next((v for k,v in json.loads(session['watermarks']).items() if k.startswith(ref['shard']+'|')),None)
        if mark and mark['generation']==ref.get('generation') and ref['local_id']<=mark['upper']: return
        inserted=self.db.execute('INSERT OR IGNORE INTO reply_triggers VALUES(?,?,?,?,NULL)',
            (item['message_id'],aid,cid,self._ai_session(aid,cid)['epoch'])).rowcount
        if inserted:
            self.db.execute('UPDATE delivery_sessions SET last_trigger_at=? WHERE account_id=? AND conversation_id=?',(time.time(),aid,cid))
            # A history refresh may have memorized this message before the live cursor reaches it.
            self.db.execute("UPDATE ai_message_state SET status='pending',memorized_at=NULL WHERE message_id=? AND status='memorized'",(item['message_id'],))
            self.db.execute('UPDATE ai_sessions SET auto_pending=1 WHERE account_id=? AND conversation_id=?',(aid,cid))

    def _delivery_reopen_pending(self, aid, cid):
        # Eligibility discovered during a memory-only model call belongs to its next round.
        self.db.execute("UPDATE ai_message_state SET status='pending',memorized_at=NULL WHERE status='memorized' AND message_id IN (SELECT message_id FROM reply_triggers WHERE account_id=? AND conversation_id=? AND epoch=? AND consumed_run_id IS NULL)",(aid,cid,self._ai_session(aid,cid)['epoch']))

    def _reply_ids(self, aid, cid, mids):
        if not mids: return []
        epoch=self._ai_session(aid,cid)['epoch']
        return [r[0] for r in self.db.execute('SELECT message_id FROM reply_triggers WHERE account_id=? AND conversation_id=? AND epoch=? AND consumed_run_id IS NULL',(aid,cid,epoch)) if r[0] in mids]

    def _delivery_blocked(self, aid, cid):
        return bool(self.db.execute("SELECT 1 FROM deliveries WHERE account_id=? AND conversation_id=? AND status IN ('failed','unknown','queued','sending') LIMIT 1",(aid,cid)).fetchone())

    def _delivery_debouncing(self, aid, cid):
        row=self.db.execute('SELECT last_trigger_at FROM delivery_sessions WHERE account_id=? AND conversation_id=?',(aid,cid)).fetchone()
        return bool(row and row[0]>time.time()-3)

    def _delivery_public(self, rid):
        parts=[dict(index=r['part_index'],status=r['status'],attempts=r['attempts'],code=r['code'],
                    archive_message_id=r['archive_message_id'],confirmation=r['confirmation'])
               for r in self.db.execute('SELECT * FROM deliveries WHERE run_id=? ORDER BY part_index',(rid,))]
        statuses={p['status'] for p in parts}
        status=next((s for s in ('unknown','sending','failed','queued','cancelled','sent') if s in statuses),'not_required')
        return dict(status=status,sent_parts=sum(p['status']=='sent' for p in parts),total_parts=len(parts),parts=parts)

    def _delivery_context(self, aid, cid):
        return [dict(run_id=r['run_id'],reply=json.loads(r['payload']).get('reply'),delivery=self._delivery_public(r['run_id']))
                for r in self.db.execute("SELECT run_id,payload FROM ai_runs WHERE account_id=? AND conversation_id=? AND status='completed' ORDER BY rowid DESC LIMIT 10",(aid,cid))]

    def _queue_delivery(self, run):
        aid,cid,rid=run['account_id'],run['conversation_id'],run['run_id']
        mids=[r[0] for r in self.db.execute('SELECT message_id FROM ai_run_messages WHERE run_id=?',(rid,))]
        triggers=[mid for mid in self._reply_ids(aid,cid,mids) if mid in run.get('reply_trigger_ids',[])]
        if not triggers:
            run['reply']=''
            return
        # A completed empty reply also consumes its trigger; edits must not create a second response.
        self.db.executemany('UPDATE reply_triggers SET consumed_run_id=? WHERE message_id=? AND consumed_run_id IS NULL',((rid,mid) for mid in triggers))
        if not run.get('reply','').strip(): return
        target=self._conversation(aid,cid)['source_id']
        for index,text in enumerate(split_reply(run['reply'])):
            self.db.execute('INSERT INTO deliveries(run_id,part_index,account_id,conversation_id,epoch,target,text,status,created_at) VALUES(?,?,?,?,?,?,?,\'queued\',?)',
                            (rid,index,aid,cid,run['epoch'],target,text,stamp()))

    def delivery_candidates(self):
        def query():
            return [dict(r) for r in self.db.execute("SELECT d.* FROM deliveries d JOIN conversation_preferences p USING(account_id,conversation_id) WHERE d.status='queued' AND p.ai_enabled=1 AND NOT EXISTS(SELECT 1 FROM deliveries x WHERE x.account_id=d.account_id AND x.conversation_id=d.conversation_id AND x.status IN ('sending','unknown','failed')) AND NOT EXISTS(SELECT 1 FROM deliveries x WHERE x.run_id=d.run_id AND x.part_index<d.part_index AND x.status<>'sent') ORDER BY d.rowid LIMIT 100")]
        return self.call(query)

    def claim_delivery(self, rid, part):
        def write():
            with self.db:
                row=self.db.execute('SELECT * FROM deliveries WHERE run_id=? AND part_index=?',(rid,part)).fetchone()
                if not row or row['status']!='queued': return None
                aid,cid=row['account_id'],row['conversation_id']
                if not self._preferences(aid,cid)['ai_enabled'] or self._ai_session(aid,cid)['epoch']!=row['epoch']:
                    self.db.execute("UPDATE deliveries SET status='cancelled',code='ai_disabled' WHERE run_id=? AND part_index=?",(rid,part)); return None
                if self.db.execute("SELECT 1 FROM deliveries WHERE account_id=? AND conversation_id=? AND status IN ('sending','unknown','failed')",(aid,cid)).fetchone(): return None
                if self.db.execute("SELECT 1 FROM deliveries WHERE run_id=? AND part_index<? AND status<>'sent'",(rid,part)).fetchone(): return None
                attempt=uuid4().hex
                self.db.execute("UPDATE deliveries SET status='sending',attempts=attempts+1,code=NULL WHERE run_id=? AND part_index=?",(rid,part))
                self.db.execute("INSERT INTO delivery_attempts VALUES(?,?,?,?,NULL,'sending',NULL)",(attempt,rid,part,stamp()))
                event=self._ai_touch(aid,cid,[])
            self.notify(event)
            return dict(row,attempt_id=attempt)
        return self.call(write)

    def finish_delivery(self, job, result):
        def write():
            state=result.get('status')
            if state not in ('sent','failed','unknown'): state='unknown'
            code=result.get('code','native_result_uncertain')[:100]
            with self.db:
                self.db.execute("UPDATE delivery_attempts SET status=?,code=?,completed_at=? WHERE attempt_id=? AND status='sending'",(state,code,stamp(),job['attempt_id']))
                if state=='failed' and code in ('source_offline','native_unavailable','native_busy','source_identity_changed','native_acceptance_required','native_version_unsupported'):
                    current=self._preferences(job['account_id'],job['conversation_id'])
                    state='queued' if current['ai_enabled'] and self._ai_session(job['account_id'],job['conversation_id'])['epoch']==job['epoch'] else 'cancelled'
                self.db.execute("UPDATE deliveries SET status=?,code=?,confirmation=? WHERE run_id=? AND part_index=? AND status='sending'",(state,code,'native' if state=='sent' else None,job['run_id'],job['part_index']))
                event=self._ai_touch(job['account_id'],job['conversation_id'],[])
            self.notify(event)
        self.call(write)

    def update_delivery(self, aid, cid, rid, resolution=None):
        def write():
            with self.db:
                if not self.db.execute('SELECT 1 FROM ai_runs WHERE run_id=? AND account_id=? AND conversation_id=?',(rid,aid,cid)).fetchone():
                    raise ConversationError('ai_run_not_found','AI 任务不存在。',404)
                status=self._delivery_public(rid)['status']
                if resolution is None:
                    if status!='failed' or not self._preferences(aid,cid)['ai_enabled']:
                        raise ConversationError('delivery_retry_invalid','只能重试明确未发送的失败项，并且需要开启 AI。',409)
                    self.db.execute("UPDATE deliveries SET status='queued',code=NULL WHERE run_id=? AND status='failed'",(rid,))
                else:
                    if status!='unknown': raise ConversationError('delivery_resolve_invalid','仅可确认结果不确定的发送。',409)
                    if resolution=='confirmed_sent':
                        self.db.execute("UPDATE deliveries SET status='sent',confirmation='user',code='user_confirmed' WHERE run_id=? AND status='unknown'",(rid,))
                    elif resolution=='abandoned':
                        self.db.execute("UPDATE deliveries SET status='cancelled',code='user_abandoned' WHERE run_id=? AND status IN ('unknown','queued','failed')",(rid,))
                    else: raise ConversationError('delivery_resolve_invalid','确认方式无效。',422)
                value=self._delivery_public(rid); event=self._ai_touch(aid,cid,[])
            self.notify(event); return value
        return self.call(write)

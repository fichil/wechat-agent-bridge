"""Immutable outgoing blobs and request-scoped, account-isolated media choices."""
import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4
from .conversations import ConversationError
from .role_storage import conflict
from .reply_media import decode_base64, validate_media, parse_reply, invalid, MAX_REPLY_MEDIA
from .delivery_storage import split_reply

ASSETS = Path(__file__).resolve().parents[1] / 'assets/dsh-pet'
SUMMARY_FIELDS = ('media_id','mime_type','size_bytes','sha256','width','height','frame_count')

@lru_cache(maxsize=1)
def builtin_stickers():
    manifest = json.loads((ASSETS/'manifest.json').read_text(encoding='utf-8'))
    values=[]
    for item in manifest['items']:
        data=(ASSETS/item['file']).read_bytes()
        if hashlib.sha1(('blob '+str(len(data))+'\0').encode()+data).hexdigest() != item['git_blob_sha1']:
            raise ConversationError('sticker_assets_invalid','内置表情素材校验失败。',503)
        values.append((item,validate_media(data,'source'),validate_media(data,'sticker')))
    return manifest,values


class MediaStoreMixin:
    def _media_put(self, value):
        mid = 'media_' + value['sha256']
        self.db.execute('INSERT OR IGNORE INTO reply_media VALUES(?,?,?,?,?,?,?,?)',
                        (mid,value['mime_type'],value['data'],value['size_bytes'],value['sha256'],value['width'],value['height'],value['frame_count']))
        return mid

    def _media_value(self, mid, data=False):
        row = self.db.execute('SELECT * FROM reply_media WHERE media_id=?', (mid,)).fetchone()
        if not row: raise ConversationError('reply_media_not_found','回复图片不存在。',404)
        value = {k:row[k] for k in SUMMARY_FIELDS}
        if data: value['data_base64'] = base64.b64encode(row['data']).decode('ascii')
        return value

    def _seed_stickers(self):
        if self.db.execute("SELECT 1 FROM metadata WHERE key='builtin_stickers_v1'").fetchone(): return
        manifest,values = builtin_stickers()
        with self.db:
            for item,original_data,sent_data in values:
                original = self._media_put(original_data)
                sent = self._media_put(sent_data)
                aid = 'dsh_' + hashlib.sha256(item['name'].encode()).hexdigest()[:16]
                self.db.execute('INSERT OR IGNORE INTO stickers VALUES(?,?,?,?,?,?,?,?,?)',
                    (aid,item['name'],item['description'],1,1,manifest['source'],1,original,sent))
            self.db.execute("INSERT INTO metadata VALUES('builtin_stickers_v1',?)",(manifest['commit'],))

    def _sticker(self, aid, data=False):
        row = self.db.execute('SELECT * FROM stickers WHERE asset_id=?',(aid,)).fetchone()
        if not row: raise ConversationError('sticker_not_found','表情包不存在。',404)
        return dict(asset_id=aid,name=row['name'],description=row['description'],enabled=bool(row['enabled']),
                    builtin=bool(row['builtin']),source=row['source'],revision=row['revision'],
                    original=self._media_value(row['original_media_id'],data),media=self._media_value(row['send_media_id'],data))

    def stickers(self):
        return self.call(lambda: dict(items=[self._sticker(r[0]) for r in self.db.execute('SELECT asset_id FROM stickers ORDER BY rowid')]))

    def sticker(self, aid):
        return self.call(self._sticker,aid,True)

    def _sticker_limit(self, enabled, exclude=''):
        if enabled and self.db.execute('SELECT count(*) FROM stickers WHERE enabled=1 AND asset_id<>?',(exclude,)).fetchone()[0] >= 100:
            raise ConversationError('sticker_catalog_full','最多同时启用 100 张表情，请先停用不常用的素材。',422)

    def create_sticker(self, fields):
        data,mime = decode_base64(fields['data_base64'],fields['mime_type'])
        original,sent = validate_media(data,'source',mime),validate_media(data,'sticker',mime)
        if not fields['name'].strip() or not fields['description'].strip(): invalid('名称和描述不能为空。')
        aid = 'sticker_' + uuid4().hex
        def write():
            with self.db:
                self._sticker_limit(fields['enabled'])
                self.db.execute('INSERT INTO stickers VALUES(?,?,?,?,?,?,?,?,?)',(aid,fields['name'].strip(),fields['description'].strip(),
                    int(fields['enabled']),0,None,1,self._media_put(original),self._media_put(sent)))
            self.notify(dict(type='changed',resources=['stickers']))
            return self._sticker(aid)
        return self.call(write)

    def update_sticker(self, aid, revision, fields):
        if not fields['name'].strip() or not fields['description'].strip(): invalid('名称和描述不能为空。')
        def write():
            with self.db:
                old=self._sticker(aid)
                if old['revision'] != revision: conflict()
                self._sticker_limit(fields['enabled'],aid)
                self.db.execute('UPDATE stickers SET name=?,description=?,enabled=?,revision=revision+1 WHERE asset_id=?',
                    (fields['name'].strip(),fields['description'].strip(),int(fields['enabled']),aid))
            self.notify(dict(type='changed',resources=['stickers']))
            return self._sticker(aid)
        return self.call(write)

    def delete_sticker(self, aid, revision):
        def write():
            with self.db:
                old=self._sticker(aid)
                if old['revision'] != revision: conflict()
                if old['builtin']: raise ConversationError('sticker_builtin','内置表情不能删除，可以停用。',409)
                self.db.execute('DELETE FROM stickers WHERE asset_id=?',(aid,))
                self._media_collect()
            self.notify(dict(type='changed',resources=['stickers']))
            return dict(asset_id=aid,deleted=True)
        return self.call(write)

    def _media_collect(self):
        self.db.execute('DELETE FROM reply_media WHERE media_id NOT IN (SELECT original_media_id FROM stickers UNION SELECT send_media_id FROM stickers UNION SELECT media_id FROM reply_candidates UNION SELECT media_id FROM deliveries WHERE media_id IS NOT NULL)')

    def prepare_reply_candidates(self, aid, cid, rid, capabilities):
        def write():
            with self.db:
                run = self._media_run(aid,cid,rid)
                if run['status'] != 'running': return dict(stickers=[],images=[],capabilities=capabilities)
                run.pop('delivery',None)
                run['reply_format_version']=2
                self._ai_write_run(run)
                self.db.execute('DELETE FROM reply_candidates WHERE run_id=?',(rid,))
                if capabilities.get('sticker'):
                    for row in self.db.execute('SELECT * FROM stickers WHERE enabled=1 ORDER BY rowid').fetchall():
                        self.db.execute('INSERT OR IGNORE INTO reply_candidates VALUES(?,?,?,?,?,?)',
                            (rid,'sticker',row['asset_id'],row['send_media_id'],row['name'],row['description']))
                if capabilities.get('image'):
                    rows=self.db.execute("SELECT m.message_id,m.payload,c.data,c.mime_type FROM messages m JOIN media_cache c ON c.cache_key='image:'||m.message_id AND c.account_id=m.account_id WHERE m.account_id=? AND m.conversation_id=? AND json_extract(m.payload,'$.type')='image' ORDER BY m.sort_key DESC LIMIT 20",(aid,cid)).fetchall()
                    for row in rows:
                        try: media=validate_media(bytes(row['data']),'image',row['mime_type'])
                        except ConversationError: continue
                        info=json.loads(row['payload']); description='本会话图片，原始时间：'+str(info.get('sent_at') or '未知')
                        self.db.execute('INSERT OR IGNORE INTO reply_candidates VALUES(?,?,?,?,?,?)',
                            (rid,'image',row['message_id'],self._media_put(media),'会话图片',description))
                return self._reply_catalog(rid,capabilities)
        return self.call(write)

    def _reply_catalog(self,rid,capabilities):
        result=dict(stickers=[],images=[],capabilities=capabilities)
        for r in self.db.execute('SELECT * FROM reply_candidates WHERE run_id=? ORDER BY rowid',(rid,)):
            key='asset_id' if r['kind']=='sticker' else 'message_id'
            result['stickers' if r['kind']=='sticker' else 'images'].append({key:r['reference'],'name':r['name'],'description':r['description']})
        return result

    def _prepare_reply(self,run,reply):
        parts=[]; total=0
        snapshot=self.db.execute('SELECT payload FROM ai_request_snapshots WHERE run_id=?',(run['run_id'],)).fetchone()
        catalog=json.loads(snapshot[0])['envelope'].get('reply_media',{}) if snapshot else {}
        for item in parse_reply(reply):
            kind=item['kind']
            if kind=='text':
                for text in split_reply(item['text']): parts.append(dict(index=len(parts),kind='text',text=text))
                continue
            if not catalog.get('capabilities',{}).get(kind): invalid('当前微信构建未提供此媒体发送能力。')
            name='图片' if kind=='image' else '表情'
            if 'media' in item:
                mid=self._media_put(item['media'])
            else:
                row=self.db.execute('SELECT * FROM reply_candidates WHERE run_id=? AND kind=? AND reference=?',
                    (run['run_id'],kind,item['reference'])).fetchone()
                if not row: invalid('回复引用的素材不在本次可用清单中。')
                mid,name=row['media_id'],row['name']
            media=self._media_value(mid);total+=media['size_bytes']
            if total>MAX_REPLY_MEDIA: invalid('一次回复的媒体总量不能超过 16 MiB。')
            parts.append(dict(index=len(parts),kind=kind,name=name,media=media))
        return parts

    def reply_media(self,aid,cid,rid,index):
        def query():
            run=self._media_run(aid,cid,rid)
            part=next((p for p in run.get('reply_parts',[]) if p['index']==index and p['kind']!='text'),None)
            if not part: raise ConversationError('reply_media_not_found','此回复没有对应图片。',404)
            return self._media_value(part['media']['media_id'],True)
        return self.call(query)

    def _media_run(self,aid,cid,rid):
        row=self.db.execute('SELECT payload FROM ai_runs WHERE run_id=? AND account_id=? AND conversation_id=?',(rid,aid,cid)).fetchone()
        if not row: raise ConversationError('ai_run_not_found','AI 任务不存在。',404)
        return json.loads(row[0])

    def delivery_media(self,job):
        def query():
            row=self.db.execute('SELECT d.media_id,m.data,m.mime_type,m.sha256 FROM deliveries d JOIN reply_media m USING(media_id) WHERE d.run_id=? AND d.part_index=? AND d.account_id=? AND d.conversation_id=?',
                                (job['run_id'],job['part_index'],job['account_id'],job['conversation_id'])).fetchone()
            if not row: raise ConversationError('reply_media_not_found','发送素材不存在。',404)
            return dict(row)
        return self.call(query)

"""Role/API-only migration: no chat, media, sessions, admin data or secrets."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from uuid import uuid4

from .migrate import offline, snapshot, sha, validate_db

FORMAT = 'wechat-agent-bridge-configuration'
LIMIT = 32 * 1024 * 1024
API_FIELDS = ('name', 'provider', 'protocol', 'base_url', 'model', 'anthropic_version', 'workspace_id')
SPEECH_FIELDS = ('speech_protocol','speech_model')


def validate(value):
    from backend.app.ai_models import ProfileFields
    from backend.app.role_models import CharacterCard
    if not isinstance(value, dict) or set(value) != {'roles', 'profiles', 'defaults'}:
        raise ValueError('配置迁移包结构不正确。')
    if not isinstance(value['roles'], list) or not isinstance(value['profiles'], list) or max(len(value['roles']), len(value['profiles'])) > 10000:
        raise ValueError('配置迁移包条目过多。')
    for key, identity, fields in (('roles','card_code',{'card_code','card'}), ('profiles','profile_id',{'profile_id',*API_FIELDS})):
        ids = set()
        for row in value[key]:
            allowed = (fields, fields | set(SPEECH_FIELDS)) if key == 'profiles' else (fields,)
            if not isinstance(row, dict) or set(row) not in allowed or not isinstance(row[identity], str) or not 1 <= len(row[identity]) <= 128 or row[identity] in ids:
                raise ValueError('配置迁移包存在重复或非法条目。')
            ids.add(row[identity])
            if key == 'roles':
                CharacterCard.model_validate(row['card'])
                from backend.app.voice_provider import validate_voice_bindings
                validate_voice_bindings(row['card'])
            else:
                ProfileFields.model_validate({k:row[k] for k in (*API_FIELDS,*SPEECH_FIELDS) if k in row})
    defaults = value['defaults']
    if not isinstance(defaults, dict) or set(defaults) != {'role_card_code','api_profile_id'}:
        raise ValueError('配置默认值不正确。')
    for key, source, field in (('role_card_code','roles','card_code'), ('api_profile_id','profiles','profile_id')):
        if defaults[key] is not None and defaults[key] not in {r[field] for r in value[source]}:
            raise ValueError('配置默认值引用不存在。')


def export_configuration(data, output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('目标文件已存在，请使用新的文件名。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with offline(data) as data, tempfile.TemporaryDirectory(prefix='config-export-', dir=output.parent) as folder:
        temp = Path(folder)
        snapshot(data/'bridge.sqlite3', temp/'snapshot.sqlite3')
        validate_db(temp/'snapshot.sqlite3', 'bridge.sqlite3')
        with closing(sqlite3.connect(temp/'snapshot.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            roles = [dict(card_code=r['card_code'], card=json.loads(r['card'])) for r in db.execute('SELECT card_code,card FROM role_cards')]
            # Deliberately never select the secret column, even as DPAPI ciphertext.
            profiles = [dict(r) for r in db.execute('SELECT profile_id,'+','.join(API_FIELDS)+' FROM api_profiles')]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='speech_profiles'").fetchone():
                speech = {r['profile_id']:r for r in db.execute('SELECT * FROM speech_profiles')}
                for profile in profiles:
                    setting = speech.get(profile['profile_id'])
                    profile.update(speech_protocol=setting['protocol'] if setting else 'auto', speech_model=setting['model'] if setting else '')
            defaults = dict(db.execute('SELECT role_card_code,api_profile_id FROM ai_defaults WHERE singleton=1').fetchone())
        value = dict(roles=roles, profiles=profiles, defaults=defaults)
        validate(value)
        payload = temp/'configuration.json'
        payload.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        if payload.stat().st_size > LIMIT:
            raise ValueError('配置迁移包超过大小限制。')
        manifest = dict(format=FORMAT,version=1,created_at=datetime.now(timezone.utc).isoformat(),
                        files={'configuration.json':dict(sha256=sha(payload),size=payload.stat().st_size)})
        with zipfile.ZipFile(temp/'package.zip','w',zipfile.ZIP_DEFLATED) as out:
            out.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
            out.write(payload,'configuration.json')
        with (temp/'package.zip').open('rb') as source, output.open('xb') as dest:
            shutil.copyfileobj(source,dest)
    return dict(path=str(output),sha256=sha(output),roles=len(roles),profiles=len(profiles),keys_included=False)


def read_configuration(archive):
    import hashlib
    with zipfile.ZipFile(archive) as src:
        infos = src.infolist()
        if len(infos) != 2 or {i.filename for i in infos} != {'manifest.json','configuration.json'} or sum(i.file_size for i in infos) > LIMIT or src.getinfo('manifest.json').file_size > 16384:
            raise ValueError('配置迁移包文件清单不合法。')
        manifest = json.loads(src.read('manifest.json'))
        if manifest.get('format') != FORMAT or manifest.get('version') != 1 or set(manifest.get('files',{})) != {'configuration.json'}:
            raise ValueError('配置迁移包版本不支持。')
        raw = src.read('configuration.json')
        expected = manifest['files']['configuration.json']
        if len(raw) != expected['size'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
            raise ValueError('配置迁移包校验失败。')
        value = json.loads(raw)
        validate(value)
        return value


def import_configuration(archive, data):
    from backend.app.storage import Store
    value = read_configuration(archive)
    with offline(data) as data, tempfile.TemporaryDirectory(prefix='config-import-',dir=data) as folder:
        temp = Path(folder)
        staged = temp/'bridge.sqlite3'
        if (data/'bridge.sqlite3').exists():
            snapshot(data/'bridge.sqlite3', staged)
            validate_db(staged, 'bridge.sqlite3')
        with closing(Store(staged)) as store:
            def install():
                timestamp = datetime.now(timezone.utc).isoformat()
                with store.db:
                    profile_map = {row['profile_id']: ('API_'+uuid4().hex if store.db.execute('SELECT 1 FROM api_profiles WHERE profile_id=?',(row['profile_id'],)).fetchone() else row['profile_id']) for row in value['profiles']}
                    for row in value['roles']:
                        if store.db.execute('SELECT 1 FROM role_cards WHERE card_code=?',(row['card_code'],)).fetchone():
                            raise ValueError('角色卡编号已存在，未覆盖目标配置。')
                        from copy import deepcopy
                        card = deepcopy(row['card'])
                        extension = card['data'].get('extensions',{}).get('wechat_agent_bridge',{})
                        if isinstance(extension,dict):
                            for field in ('voices', 'voice_instructions'):
                                if field in extension:
                                    extension[field] = {profile_map.get(pid,pid):setting for pid,setting in extension[field].items()}
                        store.db.execute('INSERT INTO role_cards VALUES(?,?,?,1,?,?)',
                                         (row['card_code'],card['data']['name'],json.dumps(card,ensure_ascii=False),timestamp,timestamp))
                    for row in value['profiles']:
                        store.db.execute('INSERT INTO api_profiles VALUES(?,?,?,?,?,?,?,?,?,1,?,?)',
                                         (profile_map[row['profile_id']],*(row[k] for k in API_FIELDS),b'',timestamp,timestamp))
                        store.db.execute('INSERT INTO speech_profiles VALUES(?,?,?)', (profile_map[row['profile_id']],row.get('speech_protocol','auto'),row.get('speech_model','')))
                    # Existing target defaults and all account/conversation choices remain intact.
                    store.db.execute('UPDATE ai_defaults SET role_card_code=COALESCE(role_card_code,?),api_profile_id=COALESCE(api_profile_id,?),revision=revision+1 WHERE singleton=1',
                                     (value['defaults']['role_card_code'],profile_map.get(value['defaults']['api_profile_id'])))
            store.call(install)
        validate_db(staged,'bridge.sqlite3')
        backup = data/'migration-backups'/('before-config-import-'+uuid4().hex)
        backup.mkdir(parents=True)
        moved, installed = [], False
        try:
            for name in ('bridge.sqlite3','bridge.sqlite3-wal','bridge.sqlite3-shm'):
                if (data/name).exists():
                    os.replace(data/name,backup/name); moved.append(name)
            os.replace(staged,data/'bridge.sqlite3'); installed = True
        except BaseException:
            if installed:
                (data/'bridge.sqlite3').unlink()
            for name in moved:
                os.replace(backup/name,data/name)
            raise
    return dict(backup=str(backup),roles=len(value['roles']),profiles=len(value['profiles']),
                message='角色卡与 API 配置已导入。请重新填写并测试 API 密钥；未导入聊天或自动运行状态。')

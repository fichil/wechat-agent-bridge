import base64
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from wechat_account_probe.avatar import (MAX_AVATAR_BYTES, AVATAR_SOURCE, detect_mime,
                                          encode_avatar, read_avatar_row, load_avatar)
from wechat_account_probe.memory import database_key
from wechat_account_probe.models import Instance, ProbeError, aggregate, instance_result
from wechat_account_probe.sqlcipher import decrypt_snapshot
from fixtures import (KEY, SALT, WXID, PAGE, avatar_database, encrypted_database,
                      encrypt_page, wal)
from wechat_compatibility import registry

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF1kAAAAASUVORK5CYII=')
JPEG = b'\xff\xd8\xff\xe0synthetic_jpeg\xff\xd9'


def encrypted_avatar(rows):
    image = avatar_database(rows)
    return b''.join(encrypt_page(image[n:n+PAGE], n//PAGE+1) for n in range(0,len(image),PAGE))


class AvatarQueryTests(unittest.TestCase):
    def test_exact_wxid_and_lossless_json_base64(self):
        image = avatar_database([(WXID,'first',PNG,1),('wxid_other_contact','second',JPEG,2)])
        avatar = read_avatar_row(image,WXID)
        wire = json.loads(json.dumps(avatar))
        self.assertEqual(wire['mime_type'],'image/png')
        self.assertEqual(base64.b64decode(wire['data_base64'],validate=True),PNG)
        self.assertNotEqual(base64.b64decode(wire['data_base64']),JPEG)

    def test_mime_signatures_and_no_unknown_jpeg_fallback(self):
        for data,mime in ((JPEG,'image/jpeg'),(PNG,'image/png'),(b'GIF89ahello','image/gif'),
                          (b'RIFF'+bytes(4)+b'WEBPVP8 hello','image/webp')):
            self.assertEqual(detect_mime(data),mime)
        with self.assertRaisesRegex(ProbeError,'format_unsupported'):
            detect_mime(b'not an image')

    def test_missing_empty_wrong_type_and_ambiguous_records(self):
        for rows, reason in (([], 'avatar_not_cached'),
                             ([(WXID,'a',b'',1)], 'avatar_empty'),
                             ([(WXID,'a',None,1)], 'avatar_empty'),
                             ([(WXID,'a','not a blob',1)], 'avatar_storage_type_invalid'),
                             ([(WXID,'a',PNG,1),(WXID,'b',JPEG,2)], 'avatar_record_ambiguous')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ProbeError,reason):
                read_avatar_row(avatar_database(rows),WXID)

    def test_oversize_blob_is_rejected_in_query(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE head_image(username TEXT,image_buffer BLOB)')
            db.execute('INSERT INTO head_image VALUES(?,zeroblob(?))',(WXID,MAX_AVATAR_BYTES+1))
            image=db.serialize()
        with self.assertRaisesRegex(ProbeError,'size_limit'):
            read_avatar_row(image,WXID)
        with self.assertRaisesRegex(ProbeError,'size_limit'):
            encode_avatar(b'\xff\xd8\xff'+bytes(MAX_AVATAR_BYTES))

    def test_encrypted_avatar_wal_only_uses_committed_picture(self):
        database=encrypted_avatar([(WXID,'a',JPEG,1)])
        new=avatar_database([(WXID,'b',PNG,2)])[PAGE:]
        old=avatar_database([(WXID,'c',JPEG,3)])[PAGE:]
        image=decrypt_snapshot(database,wal([(2,2,new),(2,0,old)]),KEY)
        self.assertEqual(base64.b64decode(read_avatar_row(image,WXID)['data_base64']),PNG)

    def test_database_and_wal_decryption_obey_deadline_checks(self):
        check=Mock(side_effect=[None,ProbeError('avatar_timeout')])
        with self.assertRaisesRegex(ProbeError,'avatar_timeout'):
            decrypt_snapshot(encrypted_avatar([(WXID,'a',PNG,1)]),b'',KEY,check=check)


class AvatarLoadingTests(unittest.TestCase):
    def test_local_avatar_is_encoded_without_export(self):
        memory=Mock()
        with patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_avatar([(WXID,'a',PNG,1)]),b'')) as snapshot, \
             patch('wechat_account_probe.memory.database_key',return_value=(KEY,SALT)):
            result=load_avatar(memory,Path('own'),WXID,time.monotonic()+60)
        self.assertEqual(result['avatar_status'],'available')
        self.assertEqual(base64.b64decode(result['avatar']['data_base64']),PNG)
        self.assertEqual(snapshot.call_args.args[0],Path('own/db_storage/head_image/head_image.db'))

    def test_missing_database_and_record_are_independent_missing_states(self):
        for side_effect,reason in ((FileNotFoundError(),'avatar_database_missing'),
                                    (ProbeError('avatar_not_cached'),'avatar_not_cached')):
            with patch('wechat_account_probe.sqlcipher.stable_snapshot',side_effect=side_effect):
                result=load_avatar(Mock(),Path('own'),WXID,time.monotonic()+60)
            self.assertEqual((result['avatar'],result['avatar_status'],result['avatar_reason']),
                             (None,'missing',reason))

    def test_wrong_key_and_avatar_timeout_do_not_raise_account_failures(self):
        with patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_avatar([(WXID,'a',PNG,1)]),b'')), \
             patch('wechat_account_probe.memory.database_key',return_value=(bytes(32),SALT)):
            result=load_avatar(Mock(),Path('own'),WXID,time.monotonic()+60)
        self.assertEqual(result['avatar_status'],'unavailable')
        self.assertEqual(result['avatar_reason'],'database_key_not_verified')
        result=load_avatar(Mock(),Path('own'),WXID,2,clock=lambda:0)
        self.assertEqual(result['avatar_reason'],'avatar_timeout')

    def test_process_epoch_changes_are_not_hidden_as_avatar_errors(self):
        memory=Mock()
        memory.check.side_effect=ProbeError('process_instance_changed')
        with self.assertRaisesRegex(ProbeError,'process_instance_changed'):
            load_avatar(memory,Path('own'),WXID,time.monotonic()+60)

    def test_avatar_stage_reserves_final_verification_time(self):
        clock=Mock(side_effect=[0,0,16])
        with patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_avatar([(WXID,'a',PNG,1)]),b'')), \
             patch('wechat_account_probe.memory.database_key',return_value=(KEY,SALT)):
            result=load_avatar(Mock(),Path('own'),WXID,60,clock=clock)
        self.assertEqual(result['avatar_reason'],'avatar_timeout')

    def test_key_scan_stops_at_the_avatar_deadline_not_the_global_deadline(self):
        now=[0]
        memory=SimpleNamespace(config_name_literals=[],check=Mock())
        def find(_needles,**kwargs):
            now[0]=16
            kwargs['check']()
            raise AssertionError('deadline not enforced')
        memory.find=Mock(side_effect=find)
        with patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_avatar([(WXID,'a',PNG,1)]),b'')):
            result=load_avatar(memory,Path('own'),WXID,60,clock=lambda:now[0])
        self.assertEqual(result['avatar_reason'],'avatar_timeout')

    def test_candidate_scan_is_reused_for_two_databases_and_stays_per_memory_object(self):
        key2=bytes(reversed(KEY))
        page1=encrypted_database()[:PAGE]
        page2=encrypt_page(avatar_database([(WXID,'a',PNG,1)])[:PAGE],1,key=key2)
        memory=SimpleNamespace(config_name_literals=[123])
        with patch('wechat_account_probe.memory.candidates_at_names',return_value={(KEY,None),(key2,None)}) as scan:
            self.assertEqual(database_key(memory,page1)[0],KEY)
            self.assertEqual(database_key(memory,page2)[0],key2)
        scan.assert_called_once()
        self.assertFalse(hasattr(SimpleNamespace(),'database_key_candidates'))


class AvatarInterfaceTests(unittest.TestCase):
    def probe(self, alias='', avatar_error=False, switch=False):
        from wechat_account_probe.probe import probe_instance
        instance=Instance(100,1,1,'4.1.15.56',registry()['profiles'][0]['builds'][0]['sha256'])
        memory=Mock()
        paths={str(Path('own')/(WXID+'_abcd')/'db_storage/MMKV/config'):1}
        memory.mapped_paths.return_value=paths
        avatar=dict(avatar=None,avatar_status='unavailable',avatar_reason='avatar_timeout') if avatar_error else \
               dict(avatar=encode_avatar(PNG),avatar_status='available',avatar_reason='')
        with patch('wechat_account_probe.native.ProcessMemory',return_value=memory), \
             patch('wechat_compatibility.bind_memory'), \
             patch('wechat_account_probe.memory.current_account',side_effect=[WXID,'wxid_other_owner' if switch else WXID]), \
             patch('wechat_account_probe.memory.contact_key',return_value=(KEY,SALT)), \
             patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_database(alias=alias),b'')), \
             patch('wechat_account_probe.avatar.load_avatar',return_value=avatar):
            return probe_instance(instance,time.monotonic()+60)

    def test_null_alias_is_success_and_avatar_failure_does_not_clear_identity(self):
        result=self.probe(avatar_error=True)
        self.assertEqual((result['status'],result['wechat_id'],result['wxid']),('complete',None,WXID))
        response=aggregate([result])
        self.assertEqual((response['schema_version'],response['exit_code']),(2,0))
        self.assertEqual(response['summary']['complete_accounts'],1)
        self.assertEqual(response['summary']['avatar_available_accounts'],0)

    def test_avatar_source_and_identity_switch(self):
        result=self.probe()
        self.assertEqual(result['sources']['avatar'],AVATAR_SOURCE)
        self.assertEqual(aggregate([result])['summary']['avatar_available_accounts'],1)
        switched=self.probe(switch=True)
        self.assertIsNone(switched['avatar'])
        self.assertIsNone(switched['wxid'])

    def test_cross_session_avatar_conflicts_are_sticky_and_do_not_clear_identity(self):
        def result(data,pid):
            return instance_result(Instance(pid,pid,1,'4.1.15.56'),wxid=WXID,nickname='同名',
                                   status='complete',avatar=encode_avatar(data),avatar_status='available',
                                   avatar_reason='',sources={'avatar':AVATAR_SOURCE})
        output=aggregate([result(PNG,1),result(JPEG,2),result(PNG,3)])
        account=output['accounts'][0]
        self.assertEqual((account['wxid'],account['status']),(WXID,'complete'))
        self.assertIsNone(account['avatar'])
        self.assertEqual(account['avatar_reason'],'avatar_instances_conflict')
        self.assertNotIn('avatar',account['sources'])

    def test_table_does_not_print_base64(self):
        from wechat_account_probe.__main__ import main
        result=aggregate([self.probe()])
        with patch('wechat_account_probe.discover_accounts',return_value=result), \
             contextlib_stdout() as out:
            self.assertEqual(main(['--run','--format','table']),0)
        self.assertIn('image/png',out.getvalue())
        self.assertNotIn(base64.b64encode(PNG).decode(),out.getvalue())


def contextlib_stdout():
    from contextlib import redirect_stdout
    return redirect_stdout(io.StringIO())


if __name__=='__main__':
    unittest.main()

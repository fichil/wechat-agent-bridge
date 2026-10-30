import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

from wechat_account_probe.models import Instance, ProbeError, instance_result, aggregate
from wechat_account_probe.memory import bind_directory, remote_string
from wechat_account_probe.sqlcipher import (decrypt_snapshot, read_own_row,
                                          committed_frames, stable_snapshot)
from fixtures import (KEY, SALT, WXID, PAGE, plain_database, encrypted_database, wal, page, encrypt_page)
from wechat_compatibility import registry

PROFILE = registry()['profiles'][0]
CORE_HASH = PROFILE['builds'][0]['sha256']


class CryptoTests(unittest.TestCase):
    def test_preallocated_tail_uses_authenticated_logical_size(self):
        encrypted = encrypted_database() + bytes(PAGE * 8)
        image = decrypt_snapshot(encrypted, b'', KEY)
        self.assertEqual(len(image), PAGE * 2)
        self.assertEqual(read_own_row(image, WXID)['wxid'], WXID)
        update = plain_database(nickname='已提交')[PAGE:]
        image = decrypt_snapshot(encrypted, wal([(2, 10, update)]), KEY)
        self.assertEqual(len(image), PAGE * 2)
        self.assertEqual(read_own_row(image, WXID)['nickname'], '已提交')

    def test_zero_inside_logical_size_is_not_treated_as_preallocation(self):
        with self.assertRaisesRegex(ProbeError, 'hmac_failed'):
            decrypt_snapshot(encrypted_database()[:PAGE] + bytes(PAGE * 9), b'', KEY)

    def test_invalid_header_counter_does_not_authorize_truncation(self):
        header = bytearray(plain_database()[:PAGE])
        struct.pack_into('>I', header, 92, 2)
        encrypted = encrypt_page(header, 1) + encrypted_database()[PAGE:] + bytes(PAGE)
        with self.assertRaisesRegex(ProbeError, 'hmac_failed'):
            decrypt_snapshot(encrypted, b'', KEY)

    def test_latest_wal_header_controls_logical_size_and_is_authenticated(self):
        header = bytearray(plain_database()[:PAGE])
        struct.pack_into('>I', header, 28, 3)
        frames = wal([(1, 0, header), (3, 10, bytes(PAGE))])
        image = decrypt_snapshot(encrypted_database() + bytes(PAGE * 8), frames, KEY)
        self.assertEqual(len(image), PAGE * 3)
        with self.assertRaisesRegex(ProbeError, 'size_invalid'):
            decrypt_snapshot(encrypt_page(header, 1) + encrypted_database()[PAGE:], b'', KEY)

    def test_fixture_is_valid_sqlite_and_selects_only_self(self):
        image = plain_database()
        with contextlib.closing(sqlite3.connect(':memory:')) as db:
            db.deserialize(image)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        fields = read_own_row(decrypt_snapshot(encrypted_database(), b'', KEY), WXID)
        self.assertEqual(fields, dict(wxid=WXID, wechat_id='synthetic_account', nickname='合成昵称'))
        self.assertNotIn('其他联系人', json.dumps(fields, ensure_ascii=False))

    def test_alias_never_falls_back_to_wxid(self):
        fields = read_own_row(decrypt_snapshot(encrypted_database(alias=''), b'', KEY), WXID)
        self.assertIsNone(fields['wechat_id'])
        self.assertEqual(fields['wxid'], WXID)

    def test_missing_self_or_invalid_schema(self):
        with self.assertRaisesRegex(ProbeError, 'missing_or_ambiguous'):
            read_own_row(plain_database(), 'wxid_missing')
        with self.assertRaisesRegex(ProbeError, 'query_failed'):
            read_own_row(bytes(PAGE), WXID)

    def test_duplicate_self_rows_are_not_silently_selected(self):
        image=plain_database()[:PAGE]+page([(WXID,'first','合成'),(WXID,'second','合成')])
        with self.assertRaisesRegex(ProbeError,'missing_or_ambiguous'):
            read_own_row(image,WXID)

    def test_wrong_key_and_tampered_nonfirst_page(self):
        with self.assertRaisesRegex(ProbeError, 'key_not_verified'):
            decrypt_snapshot(encrypted_database(), b'', bytes(32))
        tampered = bytearray(encrypted_database())
        tampered[PAGE+100] ^= 1
        with self.assertRaisesRegex(ProbeError, 'hmac_failed'):
            decrypt_snapshot(tampered, b'', KEY)

    def test_committed_wal_overrides_and_uncommitted_tail_does_not(self):
        committed = plain_database(nickname='已提交')[PAGE:]
        uncommitted = plain_database(nickname='未提交')[PAGE:]
        image = decrypt_snapshot(encrypted_database(), wal([(2, 2, committed), (2, 0, uncommitted)]), KEY)
        self.assertEqual(read_own_row(image, WXID)['nickname'], '已提交')

    def test_no_commits_leaves_main_image(self):
        update = plain_database(nickname='未提交')[PAGE:]
        image = decrypt_snapshot(encrypted_database(), wal([(2, 0, update)]), KEY)
        self.assertEqual(read_own_row(image, WXID)['nickname'], '合成昵称')

    def test_wal_reset_tail_and_partial_append(self):
        frames = bytearray(wal([(2, 2, plain_database()[PAGE:]), (2, 0, plain_database()[PAGE:])]))
        frames[32+24+PAGE+8] ^= 1
        pages, size = committed_frames(frames)
        self.assertEqual((list(pages), size), ([2], 2))
        self.assertEqual(committed_frames(frames[:32+24+PAGE+10]), (pages, size))

    def test_corrupt_wal_checksum_is_not_merged(self):
        frames = bytearray(wal([(2, 2, plain_database()[PAGE:])]))
        frames[100] ^= 1
        with self.assertRaisesRegex(ProbeError, 'frame_checksum_failed'):
            committed_frames(frames)

    def test_stable_snapshot_accepts_append_but_rejects_rewrite(self):
        replies = iter([b'db', b'wal', b'db', b'wal'])
        reader = Mock(side_effect=lambda *a, **kw: next(replies))
        self.assertEqual(stable_snapshot('contact.db', reader=reader), (b'db', b'wal'))
        self.assertEqual(reader.call_args.kwargs['prefix'], 3)
        reader = Mock(side_effect=[b'db', b'wal', b'changed']*3)
        with self.assertRaisesRegex(ProbeError, 'snapshot_changed'):
            stable_snapshot('contact.db', reader=reader)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.a = Instance(100, 123456789012345678, 1, '4.1.15.56', CORE_HASH)
        self.b = Instance(200, 123456789012345679, 2, '4.1.15.56', CORE_HASH)

    def test_directory_matches_current_config_not_latest_or_first(self):
        paths = {str(Path('root')/'old_zzzz'/'db_storage'/'MMKV'/'config'): 1,
                 str(Path('root')/(WXID+'_abcd')/'db_storage'/'MMKV'/'config'): 2}
        self.assertEqual(bind_directory(paths, WXID).name, WXID+'_abcd')
        with self.assertRaisesRegex(ProbeError, 'missing_or_ambiguous'):
            bind_directory(paths, 'wxid_different')

    def test_conflicting_paths_do_not_choose_a_directory(self):
        paths = {str(Path(root)/(WXID+'_abcd')/'db_storage'/'MMKV'/'x'): 1 for root in ('one','two')}
        with self.assertRaises(ProbeError):
            bind_directory(paths, WXID)

    def test_same_nickname_is_not_account_identity(self):
        first = instance_result(self.a, wxid=WXID, nickname='同名', status='complete')
        second = instance_result(self.b, wxid='wxid_second', nickname='同名', status='complete')
        self.assertEqual(aggregate([first,second])['summary']['confirmed_accounts'], 2)

    def test_same_account_across_sessions_merges_instances(self):
        results = [instance_result(p, wxid=WXID, status='complete') for p in (self.a,self.b)]
        output = aggregate(results)
        self.assertEqual(len(output['accounts']), 1)
        self.assertEqual(len(output['accounts'][0]['instances']), 2)

    def test_unknown_and_access_denied_are_incomplete(self):
        result = aggregate([instance_result(self.a)], [dict(pid=200,reason='access_denied')])
        self.assertEqual(result['exit_code'], 2)
        self.assertFalse(result['summary']['coverage_complete'])

    def test_remote_string_rejects_unbounded_or_malformed_lengths(self):
        memory = Mock()
        memory.read.return_value = bytes(16) + (2**32).to_bytes(8,'little') + bytes(8)
        self.assertIsNone(remote_string(memory, 100000))

    def test_cached_anchor_still_reads_current_configuration_every_time(self):
        from types import SimpleNamespace
        from wechat_account_probe.memory import current_account
        anchor,base,owner,cfg,data=0x110000,0x100000,0x220000,0x330000,0x440000
        value=[WXID]
        def read(address,size):
            if address==anchor+16-0x138:return struct.pack('<Q',owner)
            if address==owner+0x68:return struct.pack('<Q',cfg)
            if address==cfg+0x48:return struct.pack('<QQQQ',data,0,len(value[0]),31)
            if address==data:return value[0].encode()
            return b''
        memory=SimpleNamespace(compatibility_profile=PROFILE,configuration_anchors=(anchor,),account_module_bounds=(base,base+100000),
                               read=Mock(side_effect=read),find=Mock(side_effect=AssertionError('rescan')))
        paths={str(Path('synthetic')/'Weixin.dll'):base}
        self.assertEqual(current_account(memory,paths),WXID)
        value[0]='wxid_synthetic_other'
        self.assertEqual(current_account(memory,paths),value[0])

    def test_process_exit_does_not_publish_old_identity(self):
        from wechat_account_probe.probe import probe_instance
        memory = Mock()
        memory.mapped_paths.side_effect = ProbeError('process_exited')
        with patch('wechat_account_probe.native.ProcessMemory',return_value=memory):
            result = probe_instance(self.a,1000)
        self.assertEqual(result['reason'],'process_exited')
        self.assertIsNone(result['wxid'])

    def test_two_windows_sessions_and_renderers_use_owner_and_process_role(self):
        import psutil
        from wechat_account_probe.native import enumerate_instances
        processes=[]
        for pid,args in ((100,[]),(101,['--type=renderer']),(200,[]),(300,[])):
            proc=Mock(pid=pid,info={'pid':pid,'name':'Weixin.exe'})
            proc.cmdline.return_value=args
            proc.exe.return_value='synthetic/Weixin.exe'
            processes.append(proc)
        k=Mock()
        k.OpenProcess.side_effect=lambda access,inherit,pid:pid
        def session(pid,out):
            out._obj.value=1 if pid<200 else 2
            return True
        k.ProcessIdToSessionId.side_effect=session
        with patch('wechat_account_probe.native.libraries',return_value=k), \
             patch('wechat_account_probe.native.process_sid',side_effect=lambda k,pid:'other' if pid==300 else 'self'), \
             patch('wechat_account_probe.native.creation_time',side_effect=lambda k,h:h*1000), \
             patch('wechat_account_probe.native.file_version',return_value='4.1.15.56'), \
             patch('wechat_compatibility.windows.core_path',return_value='synthetic/Weixin.dll'), \
             patch('wechat_compatibility.file_identity',return_value=dict(core_sha256=CORE_HASH,profile_id=PROFILE['id'])), \
             patch.object(psutil,'process_iter',return_value=processes):
            instances,issues=enumerate_instances()
        self.assertEqual([(p.pid,p.session_id) for p in instances],[(100,1),(200,2)])
        self.assertFalse(issues)

    def test_process_access_failure_keeps_coverage_incomplete(self):
        import psutil
        from wechat_account_probe.native import enumerate_instances
        process=Mock(pid=100,info={'pid':100,'name':'Weixin.exe'})
        with patch('wechat_account_probe.native.libraries'), \
             patch('wechat_account_probe.native.process_sid',side_effect=['self',ProbeError('process_owner_unreadable')]), \
             patch.object(psutil,'process_iter',return_value=[process]):
            instances,issues=enumerate_instances()
        self.assertFalse(instances)
        self.assertEqual(issues,[dict(pid=100,reason='process_owner_unreadable')])

    def test_account_switch_discards_previously_read_id(self):
        from wechat_account_probe.probe import probe_instance
        memory = Mock()
        paths = {str(Path('root')/(WXID+'_abcd')/'db_storage'/'MMKV'/'x'): 1}
        memory.mapped_paths.return_value = paths
        with patch('wechat_account_probe.native.ProcessMemory', return_value=memory), \
             patch('wechat_compatibility.bind_memory'), \
             patch('wechat_account_probe.memory.current_account', side_effect=[WXID,'wxid_changed']), \
             patch('wechat_account_probe.memory.contact_key',return_value=(KEY,SALT)), \
             patch('wechat_account_probe.sqlcipher.stable_snapshot',return_value=(encrypted_database(),b'')):
            result = probe_instance(self.a, 1000)
        self.assertIsNone(result['wxid'])
        self.assertEqual(result['reason'], 'account_changed_during_read')
        memory.close.assert_called_once()


class BoundaryTests(unittest.TestCase):
    def test_pid_reuse_is_rejected_before_memory_reads(self):
        from wechat_account_probe.native import ProcessMemory
        memory=ProcessMemory.__new__(ProcessMemory)
        memory.instance=Instance(1,100,1,'4.1.15.56')
        memory.deadline=time.monotonic()+10
        memory.k, memory.handle=Mock(),123
        with patch('wechat_account_probe.native.creation_time',return_value=101):
            with self.assertRaisesRegex(ProbeError,'process_instance_changed'):
                memory.read(100000,32)
        memory.k.ReadProcessMemory.assert_not_called()

    def test_database_handle_is_read_only_and_shares_writes_and_delete(self):
        import ctypes
        from wechat_account_probe.native import read_shared_file
        k=Mock()
        k.CreateFileW.return_value=123
        def size(_handle,out):
            out._obj.value=7
            return True
        def read(_handle,buffer,_length,out,_overlapped):
            ctypes.memmove(buffer,b'fixture',7)
            out._obj.value=7
            return True
        k.GetFileSizeEx.side_effect=size
        k.ReadFile.side_effect=read
        with patch('wechat_account_probe.native.libraries',return_value=k):
            self.assertEqual(read_shared_file('synthetic.db',1024),b'fixture')
        args=k.CreateFileW.call_args.args
        self.assertEqual(args[1:3],(0x80000000,7))
        k.CloseHandle.assert_called_once_with(123)

    def test_timed_out_worker_is_terminated_and_next_instance_still_runs(self):
        from wechat_account_probe.probe import run_workers
        a,b=Instance(100,1,1,'4.1.15.56'),Instance(200,2,1,'4.1.15.56')
        workers=[Mock(pid=1000),Mock(pid=2000,returncode=0)]
        workers[0].communicate.side_effect=[subprocess.TimeoutExpired('synthetic',.01),(b'',b'')]
        workers[0].poll.return_value=None
        workers[1].poll.return_value=0
        workers[1].communicate.return_value=(json.dumps(instance_result(b,wxid=WXID,status='complete')).encode(),b'')
        launcher=Mock(side_effect=workers)
        results=run_workers([a,b],launcher=launcher,per_instance_timeout=.01,total_timeout=1)
        self.assertEqual(results[0]['reason'],'instance_timeout')
        self.assertEqual(results[1]['wxid'],WXID)
        workers[0].kill.assert_called_once()
        workers[1].kill.assert_not_called()
        for call in launcher.call_args_list:
            self.assertEqual(call.kwargs['creationflags'],0x08000000|0x40)

    def test_worker_disconnect_has_no_exception_details(self):
        from wechat_account_probe.probe import run_workers
        worker=Mock(pid=1000,returncode=1)
        worker.poll.return_value=1
        worker.communicate.return_value=(b'not json',b'sensitive exception')
        results=run_workers([Instance(1,2,3,'4.1.15.56')],launcher=Mock(return_value=worker))
        self.assertEqual(results[0]['reason'],'worker_disconnected')
        self.assertNotIn('sensitive',json.dumps(results))

    def test_help_and_import_do_not_import_platform_dependencies(self):
        code = "import sys; import wechat_account_probe; assert 'psutil' not in sys.modules; assert 'cryptography' not in sys.modules"
        subprocess.run([sys.executable,'-B','-c',code],check=True,capture_output=True)
        result = subprocess.run([sys.executable,'-B','-m','wechat_account_probe'],capture_output=True,
                                text=True,encoding='utf-8')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--run', result.stdout)

    def test_no_target_writes_gui_actions_or_network_in_runtime_sources(self):
        forbidden = ('WriteProcessMemory', 'SetForegroundWindow', 'SendInput', 'SetCursorPos',
                     'SetFocus(', 'SuspendThread', 'DebugActiveProcess', 'CreateRemoteThread',
                     'win32clipboard', 'pyautogui', 'socket.', 'urllib', 'requests.',
                     'psutil.Process().terminate')
        root = Path(__file__).resolve().parents[1]/'wechat_account_probe'
        for source in root.glob('*.py'):
            content = source.read_text(encoding='utf-8')
            for token in forbidden:
                self.assertNotIn(token,content,source.name)

    def test_cli_json_and_partial_exit(self):
        from wechat_account_probe.__main__ import main
        result = aggregate([instance_result(Instance(1,2,3,'4.1.15.56'))])
        with patch('wechat_account_probe.discover_accounts',return_value=result), contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(['--run','--format','json'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(out.getvalue())['summary']['confirmed_accounts'],0)

    def test_default_response_and_errors_are_json(self):
        from wechat_account_probe.__main__ import main
        with patch('wechat_account_probe.discover_accounts',return_value=aggregate([])), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--run']),0)
        self.assertTrue(json.loads(out.getvalue())['summary']['coverage_complete'])
        with patch('wechat_account_probe.discover_accounts',side_effect=ProbeError('windows_x64_required')), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['--run']),1)
        failed=json.loads(out.getvalue())
        self.assertEqual(failed['accounts'],[])
        self.assertEqual(failed['error'],'windows_x64_required')
        self.assertFalse(failed['summary']['coverage_complete'])

    def test_python_backend_also_returns_json_serializable_failures(self):
        from wechat_account_probe import discover_accounts
        with patch('wechat_account_probe.probe._discover_accounts',side_effect=ProbeError('synthetic_failure')):
            result=discover_accounts()
        self.assertEqual(json.loads(json.dumps(result))['exit_code'],1)
        self.assertEqual(result['error'],'synthetic_failure')

    def test_output_outside_local_is_rejected(self):
        from wechat_account_probe.__main__ import save_output
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProbeError,'output_must_be_under_project_local'):
                save_output(Path(directory)/'result.json',{})


if __name__ == '__main__':
    unittest.main()

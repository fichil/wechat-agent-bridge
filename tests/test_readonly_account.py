"""Synthetic read-only identities: no real desktop, account or paid API access."""
from contextlib import closing
from dataclasses import asdict
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stderr

from wechat_agent_bridge.adapters.readonly_account import (
    ReadOnlyAccountDiscovery, OwnIdentityRecordReader, verified_record_id, labeled_fields,
    OWN_PROFILE_SOURCE, RECORD_SOURCE, PUBLIC_SOURCE)
from wechat_agent_bridge.adapters.windows import WindowsAdapter
from wechat_agent_bridge.adapters.own_account_windows import WindowsAccountMetadata
from wechat_agent_bridge.console_models import Account, ConsoleError
from wechat_agent_bridge.console_service import ConsoleService
from wechat_agent_bridge.console_store import ConsoleStore, digest
from tests.test_console import SyntheticProtector


STAMP = (77, 101, 134000000000000123, 'synthetic/Weixin.exe', '4.1.15.56')
ID = 'wxid_synthetic_owner'


def own_record(**updates):
    value = dict(instance_id='77:101', wechat_id=ID, name=ID, identity_certain=True, connected=True,
                 identity_source=OWN_PROFILE_SOURCE, fingerprint=dict(pid=77,hwnd=101,
                 created=STAMP[2],version=STAMP[4]))
    value.update(updates)
    return value


class RecordTests(unittest.TestCase):
    def test_current_verified_instance_is_accepted(self):
        self.assertEqual(verified_record_id(own_record(), STAMP), ID)

    def test_mismatched_instance_pid_window_creation_or_version_is_rejected(self):
        for key, value in (('pid',88),('hwnd',202),('created',STAMP[2]+1),('version','other')):
            record = own_record()
            record['fingerprint'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ConsoleError, 'record_stale'):
                verified_record_id(record, STAMP)
        with self.assertRaisesRegex(ConsoleError, 'record_stale'):
            verified_record_id(own_record(instance_id='88:101'), STAMP)

    def test_uncertain_disconnected_or_wrong_source_is_rejected(self):
        for changes in (dict(identity_certain=False),dict(connected=False),
                        dict(identity_source='visual_only'),dict(wechat_id='not an id')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ConsoleError, 'record_unverified'):
                verified_record_id(own_record(**changes),STAMP)

    def test_only_the_exact_own_record_is_decrypted_and_database_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            protector = SyntheticProtector()
            store = ConsoleStore(directory/'gate.sqlite3',protector)
            record = own_record()
            store.put('console_identity','77:101',record)
            store.put('settings','provider',{'synthetic_secret':'must not be read'})
            store.put('grant','another-account',{'read':True})
            store.close()
            before = (directory/'gate.sqlite3').read_bytes()
            unprotect = Mock(wraps=protector.unprotect)
            reader = OwnIdentityRecordReader(directory,SimpleNamespace(unprotect=unprotect))
            self.assertEqual(reader.read('77:101'),record)
            self.assertIsNone(reader.read('88:202'))
            unprotect.assert_called_once()
            self.assertEqual((directory/'gate.sqlite3').read_bytes(),before)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertIsNone(OwnIdentityRecordReader(directory).read('77:101'))
            self.assertEqual(list(directory.iterdir()),[])

    def test_unreadable_database_and_secret_exception_are_sanitized(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'gate.sqlite3').write_bytes(b'synthetic invalid database')
            with self.assertRaisesRegex(ConsoleError,'^account_identity_record_unreadable$'):
                OwnIdentityRecordReader(directory).read('77:101')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.metadata = SimpleNamespace(windows=Mock(return_value=[101]),stamp=Mock(return_value=STAMP),
                                        own_fields=Mock(return_value={}))
        self.records = SimpleNamespace(read=Mock(return_value=own_record()))
        self.discovery = ReadOnlyAccountDiscovery(self.metadata,self.records)

    def test_verified_id_without_nickname_is_partial_and_not_id_as_name(self):
        result = self.discovery.discover()[0]
        self.assertEqual(result['wechat_id'],ID)
        self.assertEqual(result['name'],'昵称未获取')
        self.assertEqual(result['discovery_state'],'partial')
        self.assertEqual(result['discovery_reason'],'account_nickname_not_exposed')
        self.assertEqual(result['discovery_sources'],{'wechat_id':RECORD_SOURCE})
        self.assertTrue(result['identity_certain'])
        self.assertFalse(result['verified'])

    def test_cached_nickname_is_not_reused_as_current_public_field(self):
        self.records.read.return_value=own_record(name='Old cached nickname')
        self.assertEqual(self.discovery.discover()[0]['name'],'昵称未获取')

    def test_public_nickname_and_current_record_id_have_separate_sources(self):
        self.metadata.own_fields.return_value={'name':'Synthetic nickname'}
        result=self.discovery.discover()[0]
        self.assertEqual(result['discovery_state'],'identified')
        self.assertEqual(result['discovery_sources'],{'name':PUBLIC_SOURCE,'wechat_id':RECORD_SOURCE})

    def test_both_public_fields_work_without_any_saved_identity(self):
        self.records.read.return_value=None
        self.metadata.own_fields.return_value={'name':'Synthetic nickname','wechat_id':ID}
        result=self.discovery.discover()[0]
        self.assertEqual(result['discovery_state'],'identified')
        self.assertEqual(result['discovery_sources'],{'name':PUBLIC_SOURCE,'wechat_id':PUBLIC_SOURCE})

    def test_conflicting_current_sources_never_publish_an_identity(self):
        self.metadata.own_fields.return_value={'name':'Synthetic','wechat_id':'different_owner'}
        result=self.discovery.discover()[0]
        self.assertIsNone(result['wechat_id'])
        self.assertFalse(result['identity_certain'])
        self.assertEqual(result['discovery_reason'],'account_identity_sources_conflict')

    def test_stale_record_is_not_a_fallback(self):
        self.records.read.return_value=own_record(instance_id='88:202')
        result=self.discovery.discover()[0]
        self.assertIsNone(result['wechat_id'])
        self.assertEqual(result['discovery_reason'],'account_identity_record_stale')

    def test_repeated_refresh_rechecks_record_and_process(self):
        self.discovery.discover()
        self.records.read.return_value=own_record(fingerprint={})
        result=self.discovery.discover()[0]
        self.assertIsNone(result['wechat_id'])
        self.assertEqual(self.metadata.stamp.call_count,4)
        self.assertEqual(self.records.read.call_count,2)

    def test_pid_reuse_during_read_discards_fields(self):
        self.metadata.stamp.side_effect=[STAMP,(*STAMP[:2],STAMP[2]+1,*STAMP[3:])]
        result=self.discovery.discover()[0]
        self.assertFalse(result['identity_certain'])
        self.assertIsNone(result['wechat_id'])

    def test_public_field_equal_to_id_never_claims_nickname_success(self):
        self.metadata.own_fields.return_value={'name':ID}
        self.assertEqual(self.discovery.discover()[0]['discovery_state'],'partial')

    def test_missing_fields_remain_unavailable(self):
        self.records.read.return_value=None
        result=self.discovery.discover()[0]
        self.assertEqual(result['discovery_state'],'unavailable')
        self.assertFalse(result['discovery_sources'])


class PublicMetadataTests(unittest.TestCase):
    def test_labeled_fields_can_be_partial_without_inference(self):
        self.assertEqual(labeled_fields(['昵称：Synthetic']),{'name':'Synthetic','wechat_id':None})
        self.assertEqual(labeled_fields(['微信号：'+ID]),{'name':None,'wechat_id':ID})
        self.assertEqual(labeled_fields(['Synthetic', ID]),{'name':None,'wechat_id':None})

    def test_conflicting_labeled_fields_are_rejected(self):
        with self.assertRaisesRegex(ConsoleError,'sources_conflict'):
            labeled_fields(['微信号：'+ID,'微信号：different_owner'])

    def test_native_metadata_has_no_ui_action_capability(self):
        for name in ('own_profile','_popups','_avatar','_fields'):
            self.assertFalse(hasattr(WindowsAccountMetadata,name))

    def test_own_fields_reads_only_unique_own_avatar_properties(self):
        backend=WindowsAccountMetadata.__new__(WindowsAccountMetadata)
        backend.client=Mock()
        backend.uia=SimpleNamespace(TreeScope_Descendants=4,UIA_ClassNamePropertyId=30012)
        avatar=Mock(CurrentProcessId=77)
        avatar.GetRuntimeId.return_value=[1,101,2]
        avatar.GetCurrentPropertyValue.side_effect=['昵称：Synthetic','微信号：'+ID,'']
        avatars=SimpleNamespace(Length=1,GetElement=lambda _:avatar)
        nav=Mock(CurrentProcessId=77)
        nav.FindAll.return_value=avatars
        root=Mock(CurrentProcessId=77)
        root.FindAll.return_value=SimpleNamespace(Length=1,GetElement=lambda _:nav)
        backend._root=Mock(return_value=root)
        fields=backend.own_fields(101)
        self.assertEqual(fields,{'name':'Synthetic','wechat_id':ID})
        avatar.GetCurrentPattern.assert_not_called()
        root.GetCurrentPropertyValue.assert_not_called()
        self.assertEqual(avatar.GetCurrentPropertyValue.call_count,3)
        self.assertEqual(backend.client._client.CreatePropertyCondition.call_args_list[-1].args,
                         (30012,'mmui::XAvatarImage'))


class ModeTests(unittest.TestCase):
    def test_readonly_adapter_uses_only_readonly_worker_entry(self):
        adapter=WindowsAdapter(auto_discovery=True,discovery_mode='read-only')
        adapter._call=Mock(return_value=[])
        adapter.discover()
        adapter._call.assert_called_once_with('discover_read_only_accounts')
        self.assertTrue(adapter.metadata_only)
        with self.assertRaisesRegex(ConsoleError,'account_discovery_read_only'):
            adapter.probe_account('77:101')

    def test_readonly_cannot_be_constructed_with_a_client_patch(self):
        for options in (dict(allow_accessibility_patch=True),dict(gate_store=object())):
            with self.subTest(options=options), self.assertRaisesRegex(ConsoleError,'conflicts_with_live'):
                WindowsAdapter(auto_discovery=True,discovery_mode='read-only',**options)

    def test_cli_conflicts_are_rejected_before_opening_database(self):
        from wechat_agent_bridge.console import main
        for options in (['--adapter','mock'], ['--adapter','windows','--live-acceptance'],
                        ['--adapter','windows','--allow-accessibility-patch']):
            with patch('wechat_agent_bridge.console.ConsoleStore') as store, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(options+['--discovery-mode','read-only'])
                store.assert_not_called()

    def test_service_cannot_authorize_run_read_chats_or_call_a_provider(self):
        with patch('socket.socket',side_effect=AssertionError('external network forbidden')):
            store=ConsoleStore(':memory:',SyntheticProtector())
            provider=Mock()
            adapter=WindowsAdapter(auto_discovery=True,discovery_mode='read-only')
            adapter.discover=Mock(return_value=[])
            service=ConsoleService(adapter,store,provider_factory=lambda _:provider)
            try:
                service.discover()
                actions=(lambda:service.authorize('synthetic',{}),service.resume,
                         lambda:service.conversations('synthetic'),lambda:service.test_provider('text'))
                for action in actions:
                    with self.assertRaisesRegex(ConsoleError,'account_discovery_read_only'):
                        action()
                self.assertFalse(service.grants)
                self.assertEqual(store.values('grant'),[])
                self.assertEqual(provider.mock_calls,[])
                self.assertTrue(service.status()['account_discovery']['metadata_only'])
            finally:
                store.close()

    def test_readonly_timeout_never_restores_or_moves_windows(self):
        adapter=WindowsAdapter(auto_discovery=True,discovery_mode='read-only')
        adapter.process=Mock()
        adapter.process.is_alive.return_value=True
        adapter.connection=Mock()
        adapter.connection.poll.return_value=False
        adapter._stop=Mock()
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window') as restore:
            with self.assertRaisesRegex(ConsoleError,'windows_operation_timeout'):
                adapter.discover()
            restore.assert_not_called()

    def test_readonly_rejects_any_ui_restoration_protocol(self):
        adapter=WindowsAdapter(auto_discovery=True,discovery_mode='read-only')
        adapter.process=Mock()
        adapter.process.is_alive.return_value=True
        adapter.connection=Mock()
        adapter.connection.poll.return_value=True
        adapter.connection.recv.return_value={'discovery_restore':{'synthetic':True}}
        adapter._stop=Mock()
        with patch('wechat_agent_bridge.adapters.own_account_windows.restore_window') as restore:
            with self.assertRaisesRegex(ConsoleError,'read_only_discovery_protocol_error'):
                adapter.discover()
            restore.assert_not_called()


class PresentationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node required for JavaScript status checks')
    def test_partial_result_never_claims_both_fields_and_sources_remain_distinct(self):
        source=(Path(__file__).resolve().parents[1]/'wechat_agent_bridge/web/app.js').read_text(encoding='utf-8')
        functions=source[source.index('function accountDiscoveryLabel('):source.index('function renderAccountSelection(')]
        script=functions+'''\nconst a={discovery_state:'partial',wechat_id:'synthetic_owner',discovery_sources:{wechat_id:'current_process_verified_own_identity_record'}};
if(accountDiscoveryLabel(a)!=='微信号已识别，昵称未获取')throw Error('partial shown as complete');
if(!accountSourceText(a).includes('只读'))throw Error('source missing');
if(accountSourceText(a).includes('昵称来源'))throw Error('missing field given a source');
'''
        run=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)

    @unittest.skipUnless(shutil.which('node'),'Node required for JavaScript status checks')
    def test_readonly_ui_hides_grants_manual_identity_and_reply_controls(self):
        source=(Path(__file__).resolve().parents[1]/'wechat_agent_bridge/web/app.js').read_text(encoding='utf-8')
        function=source[source.index('function renderDiscoveryMode('):source.index('function renderExclusions(')]
        # Keep only this complete declaration; subsequent functions are not needed.
        function=function[:function.index('\nasync function loadChats(')]
        script='''const state={};const elements=new Map();
function element(){return {hidden:false,textContent:'',nextElementSibling:{hidden:false},querySelector:()=>element(),replaceChildren:()=>{}};}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
const document={querySelector:()=>element(),querySelectorAll:()=>[]};
const node=()=>element();const backgroundPanel=element(),loadAllButton=element(),privateScopeButton=element(),privateScopeOff=element(),privateScopeNote=element(),executionNote=element();
'''+function+'''
renderDiscoveryMode({account_discovery:{metadata_only:true}});
for(const id of ['grant-form','resume','probe-account','probe-hint','refresh-chats','sync'])if(!$(id).hidden)throw Error(id+' still visible');
if(!backgroundPanel.hidden||!privateScopeButton.hidden||!privateScopeOff.hidden)throw Error('live controls still visible');
if(!$('mode').textContent.includes('后台只读'))throw Error('readonly mode missing');
'''
        run=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)


class AcceptanceGuardTests(unittest.TestCase):
    def test_readonly_cli_does_not_open_a_browser_or_change_foreground(self):
        from wechat_agent_bridge.console import main
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as temporary:
            with patch('wechat_agent_bridge.console.DPAPIProtector',return_value=SyntheticProtector()), \
                 patch('wechat_agent_bridge.console.ConsoleStore'), \
                 patch('wechat_agent_bridge.console.ConsoleService'), \
                 patch('wechat_agent_bridge.console.WindowsAdapter') as adapter, \
                 patch('wechat_agent_bridge.console.ConsoleServer') as server, \
                 patch('wechat_agent_bridge.console.webbrowser.open') as open_browser, \
                 redirect_stdout(io.StringIO()):
                adapter.return_value.metadata_only=True
                server.return_value.serve_forever.side_effect=KeyboardInterrupt()
                self.assertEqual(main(['--adapter','windows','--discovery-mode','read-only',
                                       '--data-dir',temporary]),0)
                open_browser.assert_not_called()

    def test_verifier_is_disabled_without_explicit_run(self):
        from scripts import verify_readonly_discovery as verify
        from contextlib import redirect_stdout
        with patch.object(verify,'run') as run,redirect_stdout(io.StringIO()):
            self.assertEqual(verify.main([]),0)
            run.assert_not_called()

    def test_wait_accounts_for_production_five_second_sync_interval(self):
        from scripts.verify_readonly_discovery import wait_for_read_progress
        baseline=dict(paused=False,execution=dict(channels=[{'send_ready':True}],
                      lanes=dict(read=dict(last_success=100))),background=dict(fingerprint={'synthetic':True}))
        advanced=deepcopy(baseline)
        advanced['execution']['lanes']['read']['last_success']=106
        now=[0]
        def sleep(seconds):
            now[0]+=seconds
        production=SimpleNamespace(request=lambda _:deepcopy(baseline if now[0]<6 else advanced))
        result=wait_for_read_progress(production,baseline,sleep=sleep,clock=lambda:now[0])
        self.assertEqual(result['execution']['lanes']['read']['last_success'],106)
        self.assertEqual(now[0],6)


if __name__=='__main__':
    unittest.main()

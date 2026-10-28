import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.private_test_scope import PrivateTestScope
from scripts.contact_profile_identity import PRIVATE_HEADER,sha
from scripts.verify_contact_profile import baseline_key
from tests.test_console import SyntheticProtector
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.console_store import ConsoleStore


class PrivateScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.store=ConsoleStore(':memory:',SyntheticProtector()); self.addCleanup(self.store.close)
        self.expected='wxid_synthetic_private'; self.label='same_display_name'
        self.fp=dict(pid=77,hwnd=101,created=42)
        self.binding=dict(account=dict(wechat_id='synthetic_sender',identity_source='uia_own_profile_exact_id_user_selected_window'),fingerprint=self.fp)
        self.target=dict(identity_verified=True,target_index=0,selected_row_verified=True,draft_empty=True,value_readonly=False,
            private_header_class=PRIVATE_HEADER,input_runtime_id=[42,101,4,-100],session_runtime_id=[42,101,4,-99],
            private_header_runtime_id=[42,101,4,-98])
        path=self.root/'old.dpapi'; encrypted=SyntheticProtector().protect(b'synthetic history'); path.write_bytes(encrypted)
        self.evidence=dict(kind='bound_private_contact_identity',state='contact_profile_verified',fingerprint=self.fp,
            patch_restored=True,worker_exited=True,events_observed=0,sources_unchanged=True,cleanup_errors=[],
            account_binding_modified=False,send_invoked=False,api_called=False,worker=dict(
                identity=dict(exact_id_verified=True,expected_id_sha256=sha(self.expected)),label_sha256=sha(self.label),
                source='uia_contact_card_exact_id_after_cua_navigation',target_controls=self.target,
                private_baseline=dict(upload_eligible=False,historical=True,reverified_before_save=True,
                    coverage='accessible_loaded_tail_only',baseline_source='exact_private_identity_initial_history',
                    loaded_count=2,retained_rows=2,baseline_event_hashes=['a'*64,'b'*64],
                    protected_capture=str(path),protected_sha256=hashlib.sha256(encrypted).hexdigest())))
        self.save()
    def save(self):
        self.store.put('verified_contacts',sha(self.expected),self.evidence)
        self.store.put('private_baselines',baseline_key(self.fp,self.expected),self.evidence)
    def resolve(self,scope=None,evidence=None,binding=None):
        scope=scope or PrivateTestScope(self.expected,self.label)
        result=scope.resolve(binding or self.binding,evidence or self.evidence,'synthetic_sender',self.fp,self.store,self.root)
        return scope,result
    def test_only_the_exact_private_label_is_passed_and_identity_is_hashed(self):
        scope,target=self.resolve()
        self.assertEqual(target,self.target)
        self.assertEqual(scope.names,[self.label])
        self.assertEqual(scope.metadata()['target_kind'],'private')
        self.assertNotIn(self.expected,str(scope.metadata()))
        self.assertTrue(scope.check(self.store))
    def test_sender_process_and_own_identity_source_are_separate_from_recipient(self):
        for change in ({'account':{**self.binding['account'],'wechat_id':self.expected}},
                       {'fingerprint':{**self.fp,'created':43}},
                       {'account':{**self.binding['account'],'identity_source':'nickname'}}):
            with self.assertRaisesRegex(ConsoleError,'sender_binding_required'):
                self.resolve(binding={**self.binding,**change})
    def test_visual_only_or_incomplete_contact_reports_never_enable_scope(self):
        for change in ({'kind':'development_cua_private_identity'},{'state':'failed'},
                       {'patch_restored':False},{'worker_exited':False},{'events_observed':1},
                       {'sources_unchanged':False},{'fingerprint':{**self.fp,'created':43}}):
            with self.assertRaisesRegex(ConsoleError,'exact_id_evidence_required'):
                self.resolve(evidence={**self.evidence,**change})
    def test_wrong_recipient_label_or_group_header_is_rejected(self):
        with self.assertRaises(ConsoleError): self.resolve(PrivateTestScope('wxid_other_private',self.label))
        with self.assertRaises(ConsoleError): self.resolve(PrivateTestScope(self.expected,'other label'))
        for change in ({'private_header_class':'mmui::ChatTitleBarChatRoomView'},
                       {'target_index':1},{'draft_empty':False},{'input_runtime_id':[True]},
                       {'session_runtime_id':[]}):
            evidence=copy.deepcopy(self.evidence); evidence['worker']['target_controls'].update(change)
            with self.assertRaises(ConsoleError): self.resolve(evidence=evidence)
    def test_independent_baseline_is_required_and_cannot_be_upload_eligible(self):
        key=baseline_key(self.fp,self.expected)
        for change in ({'upload_eligible':True},{'historical':False},{'reverified_before_save':False},
                       {'baseline_event_hashes':['a'*64,'a'*64]},{'baseline_event_hashes':[['not a hash']]},
                       {'baseline_event_hashes':['bad']},{'baseline_event_hashes':['a'*64]*51},
                       {'retained_rows':1},{'loaded_count':3},{'baseline_source':'group_history'}):
            baseline=copy.deepcopy(self.evidence); baseline['worker']['private_baseline'].update(change)
            self.store.put('private_baselines',key,baseline)
            with self.assertRaisesRegex(ConsoleError,'ineligible_history_baseline_required'): self.resolve()
        self.store.put('private_baselines',key,None)
        with self.assertRaises(ConsoleError): self.resolve()
    def test_capture_hash_or_path_changes_fail_before_client_access(self):
        path=Path(self.evidence['worker']['private_baseline']['protected_capture'])
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ConsoleError,'capture_changed'): self.resolve()
        baseline=copy.deepcopy(self.evidence)
        baseline['worker']['private_baseline']['protected_capture']=str(self.root.parent/'foreign.dpapi')
        self.store.put('private_baselines',baseline_key(self.fp,self.expected),baseline)
        with self.assertRaisesRegex(ConsoleError,'local_baseline_required'): self.resolve()
    def test_contact_or_baseline_revocation_during_operation_stops(self):
        for bucket,key in (('verified_contacts',sha(self.expected)),('private_baselines',baseline_key(self.fp,self.expected))):
            self.save(); scope,_=self.resolve()
            self.store.put(bucket,key,None)
            with self.assertRaisesRegex(ConsoleError,'scope_changed_during'): scope.check(self.store)
    def test_runtime_rebuild_in_contact_record_cannot_silently_rebind(self):
        contact=copy.deepcopy(self.evidence);contact['worker']['target_controls']['session_runtime_id']=[42,101,4,-97]
        self.store.put('verified_contacts',sha(self.expected),contact)
        with self.assertRaisesRegex(ConsoleError,'contact_record_changed'): self.resolve()

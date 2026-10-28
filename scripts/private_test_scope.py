"""Exact-ID private acceptance scope; no nickname-only or group substitution."""
import hashlib
from pathlib import Path
import re

from scripts.contact_profile_identity import PRIVATE_HEADER,sha
from scripts.verify_contact_profile import baseline_key
from wechat_agent_bridge.console_models import ConsoleError


class PrivateTestScope:
    kind='private'
    def __init__(self,expected,label):
        if not isinstance(expected,str) or not re.fullmatch(r'[A-Za-z0-9_-]{6,64}',expected) or not isinstance(label,str) or not label:
            raise ConsoleError('private_test_exact_id_and_label_required')
        self.expected,self.label=expected,label
        self.names=[label]
        self.contact=self.baseline=self.key=None

    def metadata(self):
        return dict(target_kind=self.kind,recipient_id_sha256=sha(self.expected),target_label_sha256=sha(self.label))

    @staticmethod
    def _complete(report,fp,expected,label):
        if not isinstance(report,dict) or not isinstance(report.get('worker'),dict):
            raise ConsoleError('private_test_exact_id_evidence_required')
        worker=report.get('worker',{})
        if not isinstance(worker.get('identity'),dict):
            raise ConsoleError('private_test_exact_id_evidence_required')
        if (report.get('kind')!='bound_private_contact_identity' or report.get('state')!='contact_profile_verified' or
            report.get('fingerprint')!=fp or report.get('patch_restored') is not True or
            report.get('worker_exited') is not True or report.get('events_observed')!=0 or
            report.get('sources_unchanged') is not True or report.get('cleanup_errors')!=[] or
            report.get('account_binding_modified') is not False or report.get('send_invoked') is not False or
            report.get('api_called') is not False or worker.get('identity',{}).get('exact_id_verified') is not True or
            worker.get('identity',{}).get('expected_id_sha256')!=sha(expected) or
            worker.get('label_sha256')!=sha(label) or
            worker.get('source')!='uia_contact_card_exact_id_after_cua_navigation'):
            raise ConsoleError('private_test_exact_id_evidence_required')
        target=worker.get('target_controls',{})
        if not isinstance(target,dict): raise ConsoleError('private_test_target_evidence_required')
        if (target.get('identity_verified') is not True or target.get('target_index')!=0 or
            target.get('selected_row_verified') is not True or target.get('private_header_class')!=PRIVATE_HEADER or
            target.get('draft_empty') is not True or target.get('value_readonly') is not False):
            raise ConsoleError('private_test_target_evidence_required')
        for name in ('input_runtime_id','session_runtime_id','private_header_runtime_id'):
            rid=target.get(name)
            if not isinstance(rid,list) or not 1<=len(rid)<=16 or any(type(x) is not int for x in rid):
                raise ConsoleError('private_test_runtime_id_invalid')
        return target

    def resolve(self,binding,evidence,account,fingerprint,store,directory):
        identity=binding.get('account',{})
        if (identity.get('wechat_id')!=account or account==self.expected or
            identity.get('identity_source')!='uia_own_profile_exact_id_user_selected_window' or
            binding.get('fingerprint')!=fingerprint):
            raise ConsoleError('private_test_sender_binding_required')
        target=self._complete(evidence,fingerprint,self.expected,self.label)
        contact=store.get('verified_contacts',sha(self.expected),{})
        if self._complete(contact,fingerprint,self.expected,self.label)!=target:
            raise ConsoleError('private_test_contact_record_changed')
        key=baseline_key(fingerprint,self.expected)
        baseline=store.get('private_baselines',key,{})
        if self._complete(baseline,fingerprint,self.expected,self.label)!=target:
            raise ConsoleError('private_test_baseline_scope_changed')
        value=baseline['worker'].get('private_baseline',{})
        hashes=value.get('baseline_event_hashes')
        if (value.get('upload_eligible') is not False or value.get('historical') is not True or
            value.get('reverified_before_save') is not True or
            value.get('coverage')!='accessible_loaded_tail_only' or
            value.get('baseline_source')!='exact_private_identity_initial_history' or
            not isinstance(hashes,list) or len(hashes)>50 or
            any(not isinstance(x,str) or not re.fullmatch(r'[a-f0-9]{64}',x) for x in hashes) or
            len(hashes)!=len(set(hashes)) or type(value.get('retained_rows')) is not int or
            value['retained_rows']!=len(hashes) or type(value.get('loaded_count')) is not int or
            not 0<=value['loaded_count']<=512 or value['retained_rows']!=min(50,value['loaded_count'])):
            raise ConsoleError('private_test_ineligible_history_baseline_required')
        path=Path(value.get('protected_capture','')).resolve()
        if path.parent!=Path(directory).resolve() or path.suffix!='.dpapi' or path.stat().st_size>1000000:
            raise ConsoleError('private_test_local_baseline_required')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=value.get('protected_sha256'):
            raise ConsoleError('private_test_baseline_capture_changed')
        self.contact,self.baseline,self.key=contact,baseline,key
        return target

    def check(self,store):
        if (self.contact is None or store.get('verified_contacts',sha(self.expected))!=self.contact or
            store.get('private_baselines',self.key)!=self.baseline):
            raise ConsoleError('private_test_scope_changed_during_operation')
        return True

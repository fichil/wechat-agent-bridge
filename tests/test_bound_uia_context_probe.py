"""Mocked bound-context launch gates; no client or paid API access."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch
from types import SimpleNamespace

_discovery_path = sys.path[:]
try:
    from scripts import probe_bound_uia_context as p
finally:
    sys.path[:] = _discovery_path
del _discovery_path


class BoundContextTests(unittest.TestCase):
    def test_default_never_starts_coordinator(self):
        with patch.object(p.original,'run') as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main([]),0)
        run.assert_not_called()

    def test_write_or_external_pid_flags_are_not_available(self):
        for flag in ('--run','--pid'):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                p.main([flag])

    def test_worker_rejects_write_before_importing_or_using_native(self):
        pipe=Mock()
        p.inspection_worker(pipe, {}, [], {}, True)
        pipe.send.assert_called_once_with({'kind':'error','value':'inspection_write_forbidden'})
        pipe.close.assert_called_once()

    def proof(self):
        return dict(passed=True,kind='self_process_readonly_uia_context',os='synthetic-os',
                    fingerprints={'proof':'fixed'})

    def test_proof_requires_full_current_native_source_and_os(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            directory=Path(name); file=directory/'proof.json'
            with (patch.object(p.original,'DIRECTORY',directory),
                  patch.object(p.canary.cross,'complete_pass',return_value=True),
                  patch.object(p.canary,'fingerprints',return_value={'proof':'fixed'}),
                  patch.object(p.platform,'version',return_value='synthetic-os')):
                file.write_text(json.dumps(self.proof()),encoding='utf-8')
                p.require_context_canary(file)
                for change in ({'passed':False},{'os':'other'},{'fingerprints':{'proof':'other'}},
                               {'kind':'not-owned-context'}):
                    file.write_text(json.dumps(dict(self.proof(),**change)),encoding='utf-8')
                    with self.subTest(change=change), self.assertRaises(p.ConsoleError):
                        p.require_context_canary(file)

    def test_explicit_inspect_passes_only_preflight_and_fixed_worker(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            with (patch.object(p.original,'DIRECTORY',Path(name)),
                  patch.object(p,'require_context_canary'),
                  patch.object(p.original,'run',return_value={'state':'preflight_verified','attempted':False}) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(p.main(['--inspect-bound','--synthetic-evidence','proof',
                                         '--virtual-desktop','fixed-desktop']),0)
        args=run.call_args.args[0]
        self.assertIs(args.run,False)
        self.assertIs(args.preflight,True)
        self.assertIs(run.call_args.kwargs['inspection_worker'],p.inspection_worker)

    def test_client_first_is_still_only_readonly_inspection(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            with (patch.object(p.original,'DIRECTORY',Path(name)),
                  patch.object(p,'require_context_canary'),
                  patch.object(p.original,'run',return_value={'state':'preflight_verified','attempted':False}) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                p.main(['--inspect-bound','--client-first','--synthetic-evidence','proof',
                        '--virtual-desktop','fixed-desktop'])
        self.assertIs(run.call_args.args[0].run,False)
        self.assertIs(run.call_args.kwargs['inspection_worker'],p.client_first_worker)

    def test_client_first_entry_explicitly_sets_initialization_order(self):
        with patch.object(p,'inspection_worker') as worker:
            p.client_first_worker('pipe','fp','names','expected',False)
        worker.assert_called_once_with('pipe','fp','names','expected',False,client_first=True)

    def test_send_context_entry_is_always_client_first_and_readonly(self):
        with patch.object(p,'inspection_worker') as worker:
            p.send_context_worker('pipe','fp','names','expected',False)
        worker.assert_called_once_with('pipe','fp','names','expected',False,
                                      client_first=True,inspect_send=True)

    def test_send_context_cannot_use_legacy_first_order(self):
        pipe=Mock()
        with patch.object(p,'prepare_client_elements',side_effect=AssertionError('native')):
            p.inspection_worker(pipe,{},[],{},False,inspect_send=True)
        pipe.send.assert_called_once_with({'kind':'error','value':'inspection_send_requires_client_first'})

    def button_fixture(self, labels=('发送(S)',)):
        uia=SimpleNamespace(UIA_ClassNamePropertyId=1, UIA_ControlTypePropertyId=2,
            UIA_ButtonControlTypeId=50000, UIA_InvokePatternId=10000,
            IUIAutomationInvokePattern=object(), TreeScope_Descendants=4)
        buttons=[]
        for label in labels:
            button=Mock(CurrentProcessId=17,CurrentControlType=50000,CurrentName=label)
            button.GetRuntimeId.return_value=[42,1,2]
            buttons.append(button)
        collection=Mock(Length=len(buttons))
        collection.GetElement.side_effect=lambda index:buttons[index]
        container=Mock(CurrentProcessId=17)
        container.FindAll.return_value=collection
        containers=Mock(Length=1)
        containers.GetElement.return_value=container
        root=Mock();root.FindAll.return_value=containers
        prepared=(Mock(_client=Mock()),root,None,None,False,uia)
        return prepared,containers,container,collection,buttons

    def test_send_discovery_pins_only_one_semantic_button_without_invoking(self):
        prepared,_,_,_,buttons=self.button_fixture()
        bundle=p.prepare_send_element(prepared,{'pid':17})
        self.assertIs(bundle[1],buttons[0])
        self.assertEqual(bundle[3],(42,1,2))
        p.check_send_element(bundle,{'pid':17},prepared[-1])
        buttons[0].GetCurrentPattern.return_value.QueryInterface.return_value.Invoke.assert_not_called()

    def test_send_discovery_rejects_ambiguous_or_missing_button(self):
        for labels in ((),('发送','Send'),('Other',)):
            prepared,*_=self.button_fixture(labels)
            with self.subTest(labels=labels), self.assertRaisesRegex(p.ConsoleError,'inspection_send_button_ambiguous'):
                p.prepare_send_element(prepared,{'pid':17})

    def test_send_discovery_checks_container_identity_and_control_limit(self):
        for change,reason in (('container','inspection_send_container_identity_mismatch'),
                              ('limit','inspection_send_control_limit'),
                              ('button','inspection_send_identity_mismatch')):
            prepared,_,container,collection,buttons=self.button_fixture()
            if change=='container':container.CurrentProcessId=18
            elif change=='limit':collection.Length=65
            else:buttons[0].CurrentProcessId=18
            with self.subTest(change=change), self.assertRaisesRegex(p.ConsoleError,reason):
                p.prepare_send_element(prepared,{'pid':17})

    def test_recreated_send_button_is_rejected(self):
        prepared,_,_,_,buttons=self.button_fixture()
        bundle=p.prepare_send_element(prepared,{'pid':17})
        buttons[0].GetRuntimeId.return_value=[42,1,3]
        with self.assertRaisesRegex(p.ConsoleError,'inspection_send_identity_changed'):
            p.check_send_element(bundle,{'pid':17},prepared[-1])

    def test_cli_selects_send_context_worker_without_write_authority(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            with (patch.object(p.original,'DIRECTORY',Path(name)),
                  patch.object(p,'require_context_canary'),
                  patch.object(p.original,'run',return_value={'state':'preflight_verified','attempted':False}) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                p.main(['--inspect-bound','--inspect-send-context','--synthetic-evidence','proof',
                        '--virtual-desktop','fixed-desktop'])
        self.assertIs(run.call_args.args[0].run,False)
        self.assertIs(run.call_args.kwargs['inspection_worker'],p.send_context_worker)

    def test_message_schema_worker_has_no_write_path(self):
        with patch.object(p,'inspection_worker') as worker:
            p.message_schema_worker('pipe','fp','names','expected',False)
        worker.assert_called_once_with('pipe','fp','names','expected',False,
            client_first=True,inspect_send=True,inspect_messages=True)

    def test_auxiliary_worker_is_explicit_readonly_mode(self):
        with patch.object(p,'inspection_worker') as worker:
            p.message_auxiliary_worker('pipe','fp','names','expected',False)
        worker.assert_called_once_with('pipe','fp','names','expected',False,
            client_first=True,inspect_send=True,inspect_messages=True,inspect_auxiliary=True)

    def test_message_schema_cli_selects_readonly_worker(self):
        with tempfile.TemporaryDirectory(dir=p.ROOT/'.local') as name:
            with (patch.object(p.original,'DIRECTORY',Path(name)),
                  patch.object(p,'require_context_canary'),
                  patch.object(p.original,'run',return_value={'state':'preflight_verified','attempted':False}) as run,
                  contextlib.redirect_stdout(io.StringIO())):
                p.main(['--inspect-bound','--inspect-message-schema','--synthetic-evidence','proof',
                        '--virtual-desktop','fixed-desktop'])
        self.assertIs(run.call_args.args[0].run,False)
        self.assertIs(run.call_args.kwargs['inspection_worker'],p.message_schema_worker)

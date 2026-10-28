import contextlib
import io
import unittest
from unittest.mock import Mock,patch
from scripts import wait_sender_profile as wait
from wechat_agent_bridge.console_models import ConsoleError


class SenderProfileWaitTests(unittest.TestCase):
    def setUp(self):self.now=0
    def sleep(self,seconds):self.now+=seconds
    def run_wait(self,handles,tick=lambda:1,check=lambda:None):
        return wait.wait_for_window(handles,tick,check,101,clock=lambda:self.now,sleep=self.sleep,
                                    timeout=1,stable_seconds=.3)
    def test_new_window_and_idle_only_signal_readiness_never_identity(self):
        result=self.run_wait(lambda:(101,) if self.now<.1 else (101,202))
        self.assertEqual(result['candidate_hwnd'],202)
        self.assertFalse(result['identity_verified'])
    def test_existing_window_does_not_count_as_new_and_times_out(self):
        with self.assertRaisesRegex(ConsoleError,'timeout'):self.run_wait(lambda:(101,202))
    def test_continuous_input_prevents_handoff(self):
        with self.assertRaisesRegex(ConsoleError,'timeout'):
            self.run_wait(lambda:(101,) if self.now<.1 else (101,202),tick=lambda:self.now)
    def test_multiple_new_windows_reject_without_choosing_first(self):
        with self.assertRaisesRegex(ConsoleError,'ambiguous'):
            self.run_wait(lambda:(101,) if self.now<.1 else (101,202,303))
    def test_main_disappears_or_identity_check_fails(self):
        with self.assertRaisesRegex(ConsoleError,'main_missing'):
            self.run_wait(lambda:(101,) if self.now<.1 else (202,))
        with self.assertRaisesRegex(ConsoleError,'changed'):
            self.run_wait(lambda:(101,),check=Mock(side_effect=ConsoleError('changed')))
    def test_candidate_disappears_resets_wait(self):
        with self.assertRaisesRegex(ConsoleError,'timeout'):
            self.run_wait(lambda:(101,202) if .1<=self.now<.2 else (101,))
    def test_cli_default_never_binds(self):
        with patch.object(wait.subprocess,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wait.main([]),0)
            run.assert_not_called()


if __name__=='__main__':unittest.main()

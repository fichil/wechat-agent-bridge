from types import SimpleNamespace as N
import unittest
from unittest.mock import Mock
from scripts.own_profile_structure import collect,PROFILE
from wechat_agent_bridge.console_models import ConsoleError


class OwnProfileStructureTests(unittest.TestCase):
    def node(self,hwnd,cls,rid):
        return N(CurrentProcessId=7,CurrentNativeWindowHandle=hwnd,CurrentClassName=cls,
                 CurrentIsOffscreen=False,GetRuntimeId=lambda:rid)
    def setUp(self):
        self.root=self.node(101,'mmui::MainWindow',(1,))
        self.profile=self.node(0,PROFILE,(2,))
        self.matches=N(Length=1,GetElement=lambda i:self.profile)
        self.root.FindAll=Mock(return_value=self.matches)
        self.client=Mock()
        self.client._client.CreatePropertyCondition.return_value='exact-class-condition'
        self.handles=Mock(return_value=(101,))
    def run_probe(self):return collect(self.client,self.root,dict(pid=7,hwnd=101),self.handles,30012)
    def test_non_native_profile_found_without_any_text_or_body_fields(self):
        result=self.run_probe()
        self.assertEqual(result['profiles_under_main'][0]['native_hwnd'],0)
        self.assertFalse(result['identity_verified'])
        self.root.FindAll.assert_called_once_with(4,'exact-class-condition')
        self.client._client.CreatePropertyCondition.assert_called_once_with(30012,PROFILE)
        self.client.element_from_handle.assert_not_called()
    def test_no_profile_is_metadata_not_identity_failure_or_success(self):
        self.matches.Length=0
        self.assertEqual(self.run_probe()['profiles_under_main'],[])
    def test_foreign_or_wrong_class_match_never_promoted(self):
        for field,value in (('CurrentProcessId',8),('CurrentClassName','other')):
            original=getattr(self.profile,field);setattr(self.profile,field,value)
            with self.assertRaises(ConsoleError):self.run_probe()
            setattr(self.profile,field,original)
    def test_window_or_root_changes_fail_closed(self):
        self.handles.side_effect=[(101,),(101,202)]
        with self.assertRaisesRegex(ConsoleError,'topology_changed'):self.run_probe()
    def test_profile_count_limit_rejects_before_read(self):
        self.matches.Length=5
        self.matches.GetElement=Mock()
        with self.assertRaisesRegex(ConsoleError,'profile_limit'):self.run_probe()
        self.matches.GetElement.assert_not_called()


if __name__=='__main__':unittest.main()

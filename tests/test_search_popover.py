import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import inspect_search_popover as probe
from wechat_agent_bridge.console_models import ConsoleError


class Node:
    CurrentProcessId=77
    CurrentIsOffscreen=False
    CurrentIsEnabled=True
    CurrentControlType=50000
    def __init__(self,classname,runtime,children=None):self.CurrentClassName=classname;self.runtime=runtime;self.items=children or {}
    def GetRuntimeId(self):return list(self.runtime)
    def FindAll(self,scope,condition):return Collection(self.items.get(condition,[]))
    @property
    def CurrentName(self):raise AssertionError('Result Name must not be requested')
    @property
    def CurrentHelpText(self):raise AssertionError('Result HelpText must not be requested')
    @property
    def CurrentAutomationId(self):raise AssertionError('Result AutomationId may contain private text')


class Collection:
    def __init__(self,items):self.items=items;self.Length=len(items)
    def GetElement(self,index):return self.items[index]


class SearchPopoverTests(unittest.TestCase):
    def setUp(self):
        self.search=dict(query_kind='recipient_id',search_runtime_id=[42,101,9])
        self.raw=Mock();self.raw.CreatePropertyCondition.side_effect=lambda property_id,value:value
        self.raw.CreateTrueCondition.return_value='__children__'
        self.uia=SimpleNamespace(TreeScope_Descendants=4,TreeScope_Children=2,UIA_ClassNamePropertyId=1)
        icon=Node('mmui::SearchContactInfoIconView',[3])
        cell=Node('mmui::SearchContactCellView',[2]);cell.CurrentControlType=50007
        self.pop=Node(probe.POPOVER,[1],{'mmui::SearchContactCellView':[cell],'mmui::SearchContactInfoIconView':[icon]})
        self.root=Node('mmui::MainWindow',[0],{probe.POPOVER:[self.pop]})
    def collect(self,values=None):
        with patch.object(probe.readonly,'inspect_control',side_effect=values or [self.search,dict(self.search)]):
            return probe.collect(self.root,self.raw,self.uia,dict(pid=77,hwnd=101),'synthetic-label','wxid_synthetic')
    def test_default_never_accesses_client(self):
        with patch.object(probe.readonly,'run',side_effect=AssertionError),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(probe.main([]),0)
    def test_wrong_query_stops_before_reading_any_result_structure(self):
        self.root.FindAll=Mock(side_effect=AssertionError)
        with self.assertRaisesRegex(ConsoleError,'exact_query_required'):
            self.collect([dict(self.search,query_kind='display_label')])
        self.root.FindAll.assert_not_called()
    def test_only_fixed_class_counts_are_returned_without_any_contact_text_or_action(self):
        result=self.collect()
        self.assertTrue(result['popover_exposed']);self.assertFalse(result['identity_verified'])
        self.assertFalse(result['contact_text_read']);self.assertFalse(result['message_text_read']);self.assertFalse(result['action_executed'])
        counts={v['class_name']:v['count'] for v in result['records']}
        self.assertEqual(counts['mmui::SearchContactCellView'],1)
        self.assertEqual(counts['mmui::SearchContactInfoIconView'],1)
    def test_missing_popover_is_an_exposure_limit_not_proof_of_zero_contacts(self):
        self.root.items={}
        result=self.collect()
        self.assertFalse(result['popover_exposed']);self.assertEqual(result['records'],[])
        self.assertFalse(result['identity_verified'])
    def test_container_structure_never_descends_into_result_items_or_message_cells(self):
        result=Node('mmui::SearchResultItemView',[11]);result.CurrentControlType=50007
        message=Node('mmui::SearchMessageCellView',[12])
        for node in (result,message):node.FindAll=Mock(side_effect=AssertionError('Result subtree must not be walked'))
        self.pop.items['__children__']=[result,message]
        output=self.collect()['container_structure']
        self.assertEqual(len(output['records']),3)
        result.FindAll.assert_not_called();message.FindAll.assert_not_called()
    def test_duplicate_foreign_or_changed_popover_and_changed_query_are_rejected(self):
        self.root.items[probe.POPOVER]=[self.pop,self.pop]
        with self.assertRaisesRegex(ConsoleError,'ambiguous'):self.collect()
        self.setUp();self.pop.CurrentProcessId=88
        with self.assertRaisesRegex(ConsoleError,'identity_changed'):self.collect()
        self.setUp();self.pop.GetRuntimeId=Mock(side_effect=[[1],[4]])
        with self.assertRaisesRegex(ConsoleError,'identity_changed'):self.collect()
        self.setUp()
        with self.assertRaisesRegex(ConsoleError,'query_changed'):
            self.collect([self.search,dict(self.search,query_kind='other')])

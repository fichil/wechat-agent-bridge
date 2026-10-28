from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts import own_avatar_hit_test as hit
from tests.test_own_navigation_metadata import Node, UIA, FP
from tests.test_own_avatar_metadata import bounds
from wechat_agent_bridge.console_models import ConsoleError


def node(rid, rect, children=(), cls='synthetic::Widget'):
    item = Node([rid], cls, children)
    item.CurrentBoundingRectangle = bounds(rect)
    for i, child in enumerate(item.children):
        child.next = item.children[i+1] if i+1 < len(item.children) else None
    return item


class RawAvatarTests(unittest.TestCase):
    def setUp(self):
        self.head = node(3, [126, 286, 198, 358], cls='synthetic::Header')
        self.chat = node(4, [700, 200, 1500, 1600])
        self.view = node(2, [100, 200, 1500, 1600], [self.head, self.chat])
        self.root = node(1, [100, 200, 1500, 1600], [self.view], cls='mmui::MainWindow')
        self.root.CurrentNativeWindowHandle = 101
        self.raw = Mock()
        self.raw.RawViewWalker.GetFirstChildElement.side_effect = lambda n: n.children[0] if n.children else None
        self.raw.RawViewWalker.GetNextSiblingElement.side_effect = lambda n: getattr(n, 'next', None)
        self.geometry = Mock(return_value=dict(frame=[100, 200, 1500, 1600], dpi=192))
    def collect(self): return hit.collect(self.root, self.raw, UIA, FP, self.geometry)

    def test_raw_leaf_is_a_locator_and_never_identity_or_input(self):
        result = self.collect()
        self.assertEqual(result['leaves'][0]['runtime_id'], [3])
        self.assertTrue(result['leaves'][0]['precise_geometry'])
        self.assertFalse(result['leaves'][0]['identity_verified'])
        self.assertFalse(result['locator_is_identity'])
        self.assertFalse(result['action_executed'])

    def test_unrelated_chat_subtree_is_never_entered_or_its_text_read(self):
        self.chat.children = [Mock()]
        self.collect()
        self.assertNotIn(unittest.mock.call(self.chat), self.raw.RawViewWalker.GetFirstChildElement.call_args_list)

    def test_coarse_provider_leaf_does_not_qualify_a_precise_avatar(self):
        self.view.children = []
        result = self.collect()
        self.assertEqual(result['leaves'][0]['runtime_id'], [2])
        self.assertFalse(result['leaves'][0]['precise_geometry'])

    def test_overlapping_leaves_are_reported_without_choosing_first(self):
        another = node(5, [126, 286, 198, 358])
        self.head.next = another
        another.next = None
        result = self.collect()
        self.assertEqual(len(result['leaves']), 2)
        self.assertFalse(result['own_avatar_identity_verified'])

    def test_sibling_loop_and_path_cycle_are_rejected(self):
        self.head.next = self.head
        with self.assertRaisesRegex(ConsoleError, 'sibling_cycle'): self.collect()
        self.head.next = None
        self.head.children = [self.view]
        with self.assertRaisesRegex(ConsoleError, 'path_cycle'): self.collect()

    def test_foreign_process_subtree_is_skipped_without_reading_content(self):
        self.head.CurrentProcessId = 99
        result = self.collect()
        self.assertFalse(result['leaves'][0]['precise_geometry'])
        self.assertNotIn(unittest.mock.call(self.head), self.raw.RawViewWalker.GetFirstChildElement.call_args_list)

    def test_node_budget_is_hard_bounded(self):
        children = [node(i+20, [700, 200, 800, 300]) for i in range(65)]
        self.root = node(1, [100, 200, 1500, 1600], children, cls='mmui::MainWindow')
        self.root.CurrentNativeWindowHandle = 101
        with self.assertRaisesRegex(ConsoleError, 'node_limit'): self.collect()

    def test_depth_cap_cannot_qualify_a_leaf_as_precise(self):
        current = self.head
        for i in range(12):
            child = node(i+20, [126, 286, 198, 358])
            current.children = [child]
            current = child
        result = self.collect()
        self.assertTrue(result['leaves'][0]['depth_limit_reached'])
        self.assertFalse(result['leaves'][0]['precise_geometry'])

    def test_geometry_or_runtime_changes_are_rejected(self):
        self.geometry.side_effect = [dict(frame=[100, 200, 1500, 1600], dpi=192),
                                     dict(frame=[200, 200, 1600, 1600], dpi=192)]
        with self.assertRaisesRegex(ConsoleError, 'window_geometry_changed'): self.collect()
        self.geometry.side_effect = None
        self.head.GetRuntimeId = Mock(side_effect=[[3], [99], [99]])
        with self.assertRaisesRegex(ConsoleError, 'node_changed'): self.collect()


if __name__ == '__main__': unittest.main()

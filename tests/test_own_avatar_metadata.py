from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from scripts import own_avatar_metadata as avatar
from tests.test_own_navigation_metadata import Node, Nodes, UIA, FP
from wechat_agent_bridge.console_models import ConsoleError


def bounds(rect):
    return SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])


class OwnAvatarTests(unittest.TestCase):
    def test_minimized_or_hidden_window_never_uses_geometry_as_avatar_evidence(self):
        for state in (dict(visible=True, iconic=True), dict(visible=False, iconic=False)):
            geometry = Mock(side_effect=AssertionError('Minimized geometry must not be read'))
            result = avatar.collect_for_window_state(None, None, None, FP, geometry, state)
            self.assertFalse(result['geometry_inspection_usable'])
            self.assertFalse(result['own_avatar_identity_verified'])
            geometry.assert_not_called()

    def test_unknown_window_state_is_not_assumed_visible(self):
        with self.assertRaises(ConsoleError):
            avatar.collect_for_window_state(None, None, None, FP, Mock(), {})

    def setUp(self):
        self.root = Node([42, 101], 'mmui::MainWindow')
        self.root.CurrentNativeWindowHandle = 101
        self.node = Node([42, 101, 2], avatar.AVATAR)
        self.node.CurrentBoundingRectangle = bounds([126, 286, 198, 358])
        self.node.parent = self.root
        self.root.FindAll = Mock(return_value=Nodes([self.node]))
        self.raw = Mock()
        self.raw.ControlViewWalker.GetParentElement.side_effect = lambda node: getattr(node, 'parent', None)
        self.geometry = Mock(return_value=dict(frame=[100, 200, 1546, 1678], dpi=192))
    def collect(self):
        return avatar.collect(self.root, self.raw, UIA, FP, self.geometry)

    def test_left_header_metadata_is_bound_to_main_ancestor_without_identity_promotion(self):
        result = self.collect()
        self.assertEqual(result['avatar_nodes_seen'], 1)
        self.assertEqual(result['left_header_candidates'][0]['logical_rect'], [13.0, 43.0, 49.0, 79.0])
        self.assertTrue(result['left_header_candidates'][0]['bound_main_ancestor'])
        self.assertFalse(result['own_avatar_identity_verified'])
        self.assertFalse(result['action_executed'])
        self.assertFalse(result['avatar_images_read'])
        self.assertEqual([call.args[1] for call in self.raw.CreatePropertyCondition.call_args_list],
                         list(avatar.OWN_CLASSES) + [50000, 50006])

    def test_chat_area_avatars_are_not_retained_as_candidates_or_walked(self):
        self.node.CurrentBoundingRectangle = bounds([700, 1000, 772, 1072])
        result = self.collect()
        self.assertEqual(result['left_header_candidates'], [])
        self.raw.ControlViewWalker.GetParentElement.assert_not_called()

    def test_wrong_window_or_foreign_node_is_rejected_without_text(self):
        self.root.CurrentNativeWindowHandle = 999
        with self.assertRaises(ConsoleError): self.collect()
        self.root.CurrentNativeWindowHandle = 101
        self.node.CurrentProcessId = 99
        with self.assertRaises(ConsoleError): self.collect()

    def test_cycles_and_parent_depth_limit_cannot_qualify_a_candidate(self):
        parent = Node([8], 'synthetic::Parent')
        parent.parent = parent
        self.node.parent = parent
        with self.assertRaisesRegex(ConsoleError, 'parent_cycle'): self.collect()
        current = self.node
        for i in range(7):
            node = Node([i+20], 'synthetic::Parent')
            current.parent = node
            current = node
        current.parent = self.root
        result = self.collect()
        self.assertFalse(result['left_header_candidates'][0]['bound_main_ancestor'])
        self.assertEqual(len(result['left_header_candidates'][0]['route']), 6)

    def test_node_limit_and_runtime_replacement_stop_collection(self):
        self.root.FindAll.return_value = Nodes([self.node] * 65)
        with self.assertRaisesRegex(ConsoleError, 'node_limit'): self.collect()
        self.root.FindAll.return_value = Nodes([self.node])
        self.node.GetRuntimeId = Mock(side_effect=[[1], [2]])
        with self.assertRaisesRegex(ConsoleError, 'node_changed'): self.collect()

    def test_geometry_dpi_and_window_change_are_rejected(self):
        for dpi, rect in ((0, [1, 2, 3, 4]), (192, [1, 2, float('nan'), 4]), (192, [4, 2, 1, 4])):
            with self.assertRaises(ConsoleError): avatar.logical_rect([0, 0, 1000, 1000], rect, dpi)
        self.geometry.side_effect = [dict(frame=[100, 200, 1546, 1678], dpi=192),
                                     dict(frame=[200, 200, 1646, 1678], dpi=192)]
        with self.assertRaisesRegex(ConsoleError, 'window_geometry_changed'): self.collect()

    def test_offscreen_candidates_are_not_assumed_visible(self):
        self.node.CurrentIsOffscreen = True
        self.assertEqual(self.collect()['left_header_candidates'], [])

    def test_multiple_geometric_candidates_do_not_become_verified_avatar(self):
        another = Node([3], avatar.AVATAR)
        another.CurrentBoundingRectangle = self.node.CurrentBoundingRectangle
        another.parent = self.root
        self.root.FindAll.return_value = Nodes([self.node, another])
        result = self.collect()
        self.assertEqual(len(result['left_header_candidates']), 2)
        self.assertFalse(result['own_avatar_identity_verified'])

    def test_main_view_objects_are_compared_by_structure_without_selecting_first(self):
        self.node.CurrentClassName = 'mmui::MainView'
        self.node.CurrentControlType = 50000
        larger = Node([3], 'mmui::MainView')
        larger.CurrentBoundingRectangle = bounds([100, 200, 1546, 1678])
        self.root.FindAll.return_value = Nodes([larger, self.node])
        result = self.collect()
        self.assertEqual(result['class_counts']['mmui::MainView'], 2)
        self.assertEqual(len(result['left_header_candidates']), 1)
        self.assertEqual(result['left_header_candidates'][0]['runtime_id'], list(self.node.rid))
        self.assertFalse(result['own_avatar_identity_verified'])

    def test_generic_button_or_image_is_only_an_unverified_geometric_candidate(self):
        self.node.CurrentClassName = 'synthetic::OwnHead'
        self.node.CurrentControlType = UIA.UIA_ButtonControlTypeId
        result = self.collect()
        self.assertEqual(result['other_button_or_image_nodes'], 1)
        self.assertEqual(result['left_header_candidates'][0]['class_name'], 'synthetic::OwnHead')
        self.assertFalse(result['own_avatar_identity_verified'])


if __name__ == '__main__': unittest.main()

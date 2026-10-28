import unittest
from unittest.mock import Mock
from scripts import window_oleacc_hit as hit
from wechat_agent_bridge.console_models import ConsoleError


class WindowOleaccHitTests(unittest.TestCase):
    def test_numeric_port_uses_comtypes_indexed_property(self):
        class NumericOnly:
            accRole = {5:43}
            def accLocation(self, child):
                assert child == 5
                return [10,30,40,50]
        port = object.__new__(hit.HitPort)
        self.assertEqual(port.numeric(NumericOnly(),5),(43,(10,30,40,50)))

    def setUp(self):
        self.root, self.child = object(), object()
        self.port = Mock()
        self.port.create.return_value = (0, self.root)
        self.port.owner.return_value = (0, 101)
        self.port.hit.return_value = 5
        self.port.numeric.return_value = (43, (10, 30, 40, 50))
        self.geometry = Mock(return_value=dict(frame=[0,0,800,900], dpi=96))

    def run_probe(self):
        return hit.inspect_hit(self.port, 101, self.geometry)

    def test_child_id_reads_only_numeric_metadata_but_never_proves_identity(self):
        result = self.run_probe()
        self.assertEqual(result['hit_kind'], 'child_id')
        self.assertTrue(result['precise_geometry'])
        self.assertFalse(result['own_avatar_identity_verified'])
        self.port.numeric.assert_called_with(self.root,5)
        self.port.hit.assert_called_once_with(self.root,(31,59))
        self.assertFalse(result['action_executed'])

    def test_empty_hit_never_reads_numeric_metadata(self):
        self.port.hit.return_value = None
        self.assertEqual(self.run_probe()['hit_kind'],'empty')
        self.port.numeric.assert_not_called()

    def test_self_and_dispatch_are_distinct(self):
        self.port.hit.return_value = 0
        self.assertEqual(self.run_probe()['hit_kind'],'self')
        self.port.hit.return_value = self.child
        self.port.dispatch.return_value = self.child
        self.assertEqual(self.run_probe()['hit_kind'],'object')
        self.port.numeric.assert_called_with(self.child,0)

    def test_foreign_returned_object_rejected_before_properties(self):
        self.port.hit.return_value = self.child
        self.port.dispatch.return_value = self.child
        self.port.owner.side_effect = [(0,101),(0,999)]
        def check(hwnd):
            if hwnd == 999: raise ConsoleError('foreign')
        self.port.require_window.side_effect = check
        with self.assertRaises(ConsoleError): self.run_probe()
        self.port.numeric.assert_not_called()

    def test_bad_variants_rejected(self):
        for value in (True,False,-1,0x80000000,'text',b'value',[],{},1.5):
            with self.subTest(value=value), self.assertRaises(ConsoleError):
                hit.resolve_hit(self.port,self.root,value)
        self.port.dispatch.assert_not_called()

    def test_missing_owner_rejected_before_hit_or_properties(self):
        self.port.owner.return_value = (0,0)
        with self.assertRaises(ConsoleError): self.run_probe()
        self.port.hit.assert_not_called()
        self.port.numeric.assert_not_called()

    def test_object_change_and_geometry_change_fail_closed(self):
        self.port.numeric.side_effect = [(43,(10,30,40,50)),(43,(10,31,40,50))]
        with self.assertRaisesRegex(ConsoleError,'object_changed'): self.run_probe()
        self.port.numeric.side_effect = None
        self.geometry.side_effect = [dict(frame=[0,0,800,900],dpi=96),dict(frame=[1,0,801,900],dpi=96)]
        with self.assertRaisesRegex(ConsoleError,'root_changed'): self.run_probe()

    def test_bad_role_location_or_off_point_fails(self):
        for value in (('button',(10,30,40,50)),(True,(10,30,40,50)),(43,(10,30,0,50)),
                      (43,(100,100,40,50)),(43,(10,30,40.0,50))):
            self.port.numeric.return_value=value
            with self.subTest(value=value), self.assertRaises(ConsoleError): self.run_probe()

    def test_coarse_root_geometry_never_becomes_avatar(self):
        self.port.numeric.return_value=(10,(0,0,800,900))
        self.assertFalse(self.run_probe()['precise_geometry'])

    def test_dpi_scaled_point_and_rect(self):
        self.geometry.return_value=dict(frame=[100,200,1700,2000],dpi=192)
        self.port.numeric.return_value=(43,(120,260,80,100))
        self.assertTrue(self.run_probe()['precise_geometry'])
        self.port.hit.assert_called_once_with(self.root,(162,318))

    def test_chain_reaches_child_without_names_or_actions(self):
        self.port.identity.side_effect = lambda obj: id(obj)
        self.port.hit.side_effect = [self.child, 5]
        self.port.dispatch.return_value = self.child
        result = hit.inspect_hit(self.port,101,self.geometry,max_depth=6)
        self.assertEqual(result['hit_calls'],2)
        self.assertEqual(result['hit_kind'],'child_id')
        self.assertFalse(result['own_avatar_identity_verified'])

    def test_same_identity_stops_and_cycle_is_rejected(self):
        self.port.identity.side_effect = lambda obj: id(obj)
        self.port.dispatch.side_effect = lambda obj: obj
        self.port.hit.return_value = self.root
        result = hit.inspect_hit(self.port,101,self.geometry,max_depth=6)
        self.assertEqual(result['termination'],'same_com_object')
        self.port.hit.side_effect = [self.child,self.root]
        with self.assertRaisesRegex(ConsoleError,'cycle'):
            hit.inspect_hit(self.port,101,self.geometry,max_depth=6)

    def test_depth_limit_cannot_promote_precise_geometry(self):
        third=object()
        self.port.identity.side_effect = lambda obj: id(obj)
        self.port.dispatch.side_effect = lambda obj: obj
        self.port.hit.side_effect = [self.child,third]
        result=hit.inspect_hit(self.port,101,self.geometry,max_depth=2)
        self.assertTrue(result['depth_limit_reached'])
        self.assertFalse(result['precise_geometry'])

    def test_invalid_depth_or_identity_stops_before_hit(self):
        for depth in (0,7,True):
            with self.assertRaises(ConsoleError):
                hit.inspect_hit(self.port,101,self.geometry,max_depth=depth)
        self.port.identity.return_value = 0
        with self.assertRaises(ConsoleError):
            hit.inspect_hit(self.port,101,self.geometry,max_depth=6)
        self.port.hit.assert_not_called()


if __name__=='__main__': unittest.main()

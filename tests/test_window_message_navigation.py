import unittest
from unittest.mock import Mock
from wechat_agent_bridge.adapters.window_message_navigation import center,WindowMessages
from wechat_agent_bridge.console_models import ConsoleError


class MessageNavigationTests(unittest.TestCase):
    def test_center_requires_complete_current_client_containment(self):
        self.assertEqual(center((20,40,100,80),(0,0,200,200)),(60,60))
        for rect in ((-1,0,10,10),(0,0,201,10),(0,0,0,10),(True,0,10,10)):
            with self.subTest(rect=rect),self.assertRaises(ConsoleError):center(rect,(0,0,200,200))

    def test_exact_down_up_without_retry(self):
        p=object.__new__(WindowMessages);p.send=Mock();p.click((3,4))
        self.assertEqual([x.args for x in p.send.call_args_list],[(0x201,1,3|(4<<16)),(0x202,0,3|(4<<16))])
        p.send.reset_mock();p.send.side_effect=ConsoleError('timeout')
        with self.assertRaises(ConsoleError):p.click((3,4))
        p.send.assert_called_once()

    def test_bad_point_never_sends(self):
        p=object.__new__(WindowMessages);p.send=Mock()
        with self.assertRaises(ConsoleError):p.click((-1,4))
        p.send.assert_not_called()

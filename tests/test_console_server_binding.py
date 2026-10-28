import socket
import unittest
from wechat_agent_bridge.console_http import ConsoleServer


class ConsoleServerBindingTests(unittest.TestCase):
    def test_another_preview_cannot_share_console_port(self):
        server = ConsoleServer(None)
        self.addCleanup(server.server_close)
        other = socket.socket()
        self.addCleanup(other.close)
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with self.assertRaises(OSError):
            other.bind(server.server_address)

    def test_console_refuses_occupied_preview_port(self):
        preview = socket.socket()
        self.addCleanup(preview.close)
        preview.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        preview.bind(('127.0.0.1', 0)); preview.listen()
        with self.assertRaises(OSError):
            ConsoleServer(None, preview.getsockname()[1])

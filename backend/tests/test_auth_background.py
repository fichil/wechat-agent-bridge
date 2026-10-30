"""Logout never cancels background AI, archive writes, or the synthetic sender."""
import threading
import time
from unittest.mock import Mock, patch
from test_roles import RoleTestCase
import test_delivery as delivery


class AuthBackgroundTests(RoleTestCase):
    configured = delivery.DeliveryTests.configured
    delta = delivery.DeliveryTests.delta
    start = delivery.DeliveryTests.start
    wait_run = delivery.DeliveryTests.wait_run

    def test_background_pipeline_completes_after_logout(self):
        self.configured()
        self.app.state.discovery.scan()
        self.delta()
        entered, release = threading.Event(), threading.Event()
        generate = self.provider.generate

        def blocked(*args):
            entered.set()
            if not release.wait(5): raise RuntimeError('synthetic wait timed out')
            return generate(*args)

        with patch.object(self.provider, 'generate', side_effect=blocked):
            run = self.start()
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.post('auth/logout').status_code, 200)
                self.assertEqual(self.post('accounts/query').status_code, 401)
            finally:
                release.set()
            self.assertEqual(self.wait_run(run)['status'], 'completed')
        sender = self.app.state.sender
        transport = Mock()
        transport.inspect.return_value = {'ready': True}
        transport.send.return_value = {'status': 'sent', 'code': 'synthetic'}
        with patch.object(sender, 'transport', transport):
            sender.tick()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = self.store.ai_run(self.ids[0], self.cids[0], run['run_id'])
                if result['delivery']['status'] == 'sent': break
                time.sleep(.01)
            self.assertEqual(result['delivery']['status'], 'sent')
        transport.send.assert_called_once()
        self.delta(2)
        self.assertEqual(len(self.store.messages(self.ids[0], self.cids[0])['items']), 2)

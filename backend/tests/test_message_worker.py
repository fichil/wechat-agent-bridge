"""Real owned-process lifecycle only; never contacts WeChat or opens its databases."""
import os
import time
import unittest

import psutil
from wechat_account_probe.models import Instance
from wechat_conversations.client import Worker


@unittest.skipUnless(os.name == "nt", "Windows process job")
class WorkerCleanupTests(unittest.TestCase):
    def test_close_waits_for_interpreter_descendants(self):
        worker = Worker(Instance(0, 0, 0, "synthetic"), "synthetic")
        try:
            root = psutil.Process(worker.process.pid)
            deadline = time.monotonic()+3
            while not root.children(recursive=True) and time.monotonic() < deadline:
                time.sleep(.01)
            processes = [root, *root.children(recursive=True)]
        finally:
            worker.close()
        self.assertTrue(all(not p.is_running() for p in processes))

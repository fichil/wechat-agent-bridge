import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wechat_sender import compatibility


class NativeAcceptanceTests(unittest.TestCase):
    def test_transport_report_cannot_enable_unverified_drafts_or_other_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'acceptance.json'
            data=dict(passed=True,accounts=['synthetic'],core_sha256='core',dll_sha256='dll')
            def write(): path.write_text(json.dumps(data),encoding='utf-8')
            with patch.object(compatibility,'ACCEPTANCE',path):
                write()
                self.assertFalse(compatibility.accepted('synthetic','core','dll'))
                data['checks']={name:'passed' for name in compatibility.REQUIRED_CHECKS}
                write()
                self.assertTrue(compatibility.accepted('synthetic','core','dll'))
                for args in [('other','core','dll'),('synthetic','changed','dll'),('synthetic','core','changed')]:
                    self.assertFalse(compatibility.accepted(*args))
                data['checks']['drafts']='inconclusive';write()
                self.assertFalse(compatibility.accepted('synthetic','core','dll'))

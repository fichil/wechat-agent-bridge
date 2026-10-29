import json
import unittest

from scripts.verify_account_probe import avatar_metadata
from wechat_account_probe.avatar import AVATAR_SOURCE, encode_avatar
from wechat_account_probe.models import Instance, instance_result
from test_avatar import PNG


class AvatarAcceptanceTests(unittest.TestCase):
    def test_report_contains_metadata_but_no_avatar_payload(self):
        account = instance_result(Instance(1,2,1,'4.1.15.56'),wxid='wxid_synthetic_owner',
                                  avatar=encode_avatar(PNG),avatar_status='available',avatar_reason='',
                                  sources={'avatar':AVATAR_SOURCE})
        metadata = avatar_metadata(account)
        self.assertTrue(metadata['valid'])
        self.assertEqual(metadata['byte_length'],len(PNG))
        serialized=json.dumps(metadata)
        self.assertNotIn('data_base64',serialized)
        self.assertNotIn(account['avatar']['data_base64'],serialized)
        self.assertNotIn(account['wxid'],serialized)

    def test_invalid_base64_and_mime_are_reported_without_echoing_payload(self):
        account=instance_result(Instance(1,2,1,'4.1.15.56'),wxid='wxid_synthetic_owner',
                                avatar={'data_base64':'sensitive invalid encoding','mime_type':'image/jpeg'},
                                avatar_status='available',avatar_reason='',sources={'avatar':AVATAR_SOURCE})
        metadata=avatar_metadata(account)
        self.assertFalse(metadata['valid'])
        self.assertNotIn('sensitive',json.dumps(metadata))
        account['avatar']=encode_avatar(PNG)
        account['avatar']['mime_type']='image/jpeg'
        self.assertFalse(avatar_metadata(account)['valid'])


if __name__=='__main__':
    unittest.main()

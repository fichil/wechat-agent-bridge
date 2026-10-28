"""Synthetic addresses only; never calls native stack capture."""
import unittest
from scripts.owned_notification_stack import normalize_frames, MAX_FRAMES


class StackTests(unittest.TestCase):
    def test_only_allowlisted_module_rvas_are_retained(self):
        modules = [dict(name='user32.dll', base=0x100000, size=0x1000)]
        self.assertEqual(normalize_frames([0x100001, 0x200000], modules),
            dict(frames=[dict(module='user32.dll', rva=1)], unknown_frames=1, frame_count=2,
                 frame_limit_reached=False))

    def test_fixed_ceiling_is_explicitly_reported(self):
        self.assertTrue(normalize_frames([0x10000] * MAX_FRAMES, [])['frame_limit_reached'])

    def test_invalid_frames_stop(self):
        for frames in ([True], [0], [None], [0x10000] * (MAX_FRAMES + 1)):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                normalize_frames(frames, [])

    def test_overlapping_modules_stop(self):
        modules = [dict(name=n, base=0x100000, size=0x1000)
                   for n in ('user32.dll', 'ntdll.dll')]
        with self.assertRaisesRegex(ValueError, 'stack_module_overlap'):
            normalize_frames([0x100010], modules)

    def test_unapproved_module_is_not_exported(self):
        with self.assertRaisesRegex(ValueError, 'stack_module_unverified'):
            normalize_frames([0x100010], [dict(name='private.dll', base=0x100000, size=0x1000)])

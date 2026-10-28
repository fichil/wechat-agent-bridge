"""Pure synthetic instruction records: no Capstone, DLL, or live client."""
import unittest

from scripts.review_activation_candidates import direct_transfers, first_argument_evidence


def instruction(address, op, args, writes=()):
    return {"rva": address, "op": op, "args": args, "writes": writes}


class StaticActivationCandidateTests(unittest.TestCase):
    def test_conditional_tail_is_recorded(self):
        records = [instruction(0x100, "je", "0x200")]
        self.assertEqual(direct_transfers(records, 0x100, 0x110),
                         [{"site": 0x100, "op": "je", "target": 0x200, "conditional": True}])

    def test_local_branch_and_indirect_call_are_not_direct_edges(self):
        records = [instruction(0x100, "jne", "0x108"),
                   instruction(0x102, "call", "qword ptr [rax + 0xf8]")]
        self.assertEqual(direct_transfers(records, 0x100, 0x110), [])

    def test_leaf_tail_jump_is_recorded_without_unwind_metadata(self):
        records = [instruction(0x100, "jmp", "0x900")]
        self.assertEqual(direct_transfers(records, 0x100, 0x105)[0]["target"], 0x900)

    def test_zero_first_argument_rejects_naive_non_null_path(self):
        records = [instruction(1, "xor", "ecx, ecx", ["ecx"]),
                   instruction(2, "mov", "edx, 3", ["edx"]),
                   instruction(3, "call", "0x900")]
        self.assertEqual(first_argument_evidence(records, 2),
                         {"state": "constant_zero", "assignment_site": 1})

    def test_rewrite_is_unknown_not_assumed_non_null(self):
        records = [instruction(1, "xor", "ecx, ecx", ["ecx"]),
                   instruction(2, "mov", "rcx, rdi", ["rcx"]),
                   instruction(3, "call", "0x900")]
        self.assertEqual(first_argument_evidence(records, 2)["state"], "unknown")

    def test_intervening_call_or_branch_invalidates_local_zero(self):
        for op in ("call", "je", "jmp"):
            with self.subTest(op=op):
                records = [instruction(1, "xor", "ecx, ecx", ["ecx"]),
                           instruction(2, op, "0x500"),
                           instruction(3, "call", "0x900")]
                self.assertEqual(first_argument_evidence(records, 2), {"state": "unknown"})


if __name__ == "__main__":
    unittest.main()

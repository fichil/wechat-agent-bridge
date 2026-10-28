"""Exact-file send-widget metadata; never loads a DLL or opens a process."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import find_qt_metaobjects

CLASSES = ('mmui::XOutlineButton', 'mmui::XButton', 'mmui::ChatInputView')


def review_send_dispatch(pe, chains):
    """Verify the reviewed exact-file dispatch, without executing Qt methods."""
    def exact(rva, expected):
        offset = pe.offset(rva, len(expected))
        if pe.data[offset:offset + len(expected)] != expected:
            raise ValueError('send_dispatch_code_mismatch')

    callback = bytes.fromhex(
        '564883ec2083f901741c85c975124885d2740d4889d14883c4205ee9488b9205'
        '4883c4205ec3488b7210b90500000031d2e85a7c1aff4889f1ba03000000'
        '4883c4205ee958defeff')
    branch = bytes.fromhex('498b41088b10488b4424404831e04c8b055d98a70a4939c00f85a10000004883c448e95b803601')
    dispatch = bytes.fromhex(
        '4883ec48488b05959ba70a4831e0488944244083fa0a745385d20f85b4030000'
        '4183f80d0f87aa0300004489c0488d15282b7c08486304824801d0ffe0')
    for address, expected in ((0x1C231B0, callback), (0x8A8CA0, dispatch), (0x8A8FCE, branch)):
        exact(address, expected)
    methods = chains['mmui::ChatInputView'][0]['methods']
    if (chains['mmui::ChatInputView'][0]['metacall'] != 0x8A8CA0 or
            len(methods) <= 8 or {key: methods[8][key] for key in ('name', 'argc', 'kind')} !=
            dict(name='Send', argc=1, kind='slot')):
        raise ValueError('send_dispatch_metadata_mismatch')
    # The reviewed metacall dispatches InvokeMetaMethod(0), r8d <= 13, using
    # signed relative entries. Method 8 must reach the argument-loading branch.
    table = 0x906B7FC
    if table + pe.unpack('<i', pe.offset(table + 8 * 4, 4))[0] != 0x8A8FCE:
        raise ValueError('send_dispatch_table_mismatch')
    destination = 0x1C11050
    callback_target = 0x1C231B0 + len(callback) + struct.unpack('<i', callback[-4:])[0]
    slot_target = 0x8A8FCE + len(branch) + struct.unpack('<i', branch[-4:])[0]
    if callback_target != destination or slot_target != destination:
        raise ValueError('send_dispatch_destination_mismatch')
    inherited = sum(sum(method['kind'] == 'signal' for method in record['methods'])
                    for record in chains['mmui::XOutlineButton'][1:])
    first = chains['mmui::XOutlineButton'][0]['methods'][0]
    if inherited != 7 or first['name'] != 'clicked' or first['kind'] != 'signal':
        raise ValueError('send_signal_metadata_mismatch')
    return dict(clicked_signal_index=inherited, callback_rva=0x1C231B0,
                callback_argument=3, send_slot_index=8, send_implementation_rva=destination,
                callback_code_sha256=hashlib.sha256(callback).hexdigest(),
                metacall_branch_sha256=hashlib.sha256(branch).hexdigest(),
                execution_proven=False, external_invocation_api=False)


def inspect(data):
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ValueError('unreviewed_dll_fingerprint')
    pe = PE(data)
    if pe.machine != 0x8664 or pe.width != 8:
        raise ValueError('send_image_architecture_mismatch')
    base = pe.unpack('<Q', pe.unpack('<I', 0x3c)[0] + 48)[0]
    chains = {}
    for name in CLASSES:
        candidates = find_qt_metaobjects(pe, base, name)
        if len(candidates) != 1:
            raise ValueError('send_class_metadata_missing_or_ambiguous')
        chains[name] = candidates[0]
    return dict(sha256=DLL_SHA256, class_chains=chains,
                send_dispatch=review_send_dispatch(pe, chains), process_accessed=False,
                runtime_object_methods_invoked=False, write_permission=False,
                limitation='Static declared metadata; signals or slots are not an external invocation API.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.output.resolve().is_relative_to((ROOT / '.local/live-acceptance').resolve()):
        parser.error('output_must_be_local_acceptance_evidence')
    if args.output.exists(): parser.error('existing_evidence_must_not_be_overwritten')
    report = inspect(args.dll.read_bytes())
    with args.output.open('x', encoding='utf-8') as output: json.dump(report, output, indent=2)
    print(json.dumps(dict(path=str(args.output), class_chains={key: [x['class'] for x in chain]
                     for key, chain in report['class_chains'].items()}, write_permission=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

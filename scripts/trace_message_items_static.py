"""Exact-file Qt message metadata only. Never loads DLLs or opens processes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.adapters.accessibility_gate import DLL_SHA256
from wechat_agent_bridge.adapters.pe_inventory import PE
from wechat_agent_bridge.adapters.static_types import qt_inheritance

ANCHORS = ((0x904F970, 'mmui::ChatTextItemView'), (0x904FA00, 'mmui::ChatItemView'))


def inspect(data):
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ValueError('unreviewed_dll_fingerprint')
    pe = PE(data)
    if pe.machine != 0x8664 or pe.width != 8:
        raise ValueError('message_image_architecture_mismatch')
    base = pe.unpack('<Q', pe.unpack('<I', 0x3c)[0] + 48)[0]
    chains = []
    for rva, expected in ANCHORS:
        chain = qt_inheritance(pe, base, rva)
        if chain[0]['class'] != expected or not any(x['class'] == 'mmui::ChatItemView' for x in chain):
            raise ValueError('message_class_anchor_mismatch')
        chains.append(chain)
    return dict(sha256=DLL_SHA256, class_chains=chains, runtime_binding_verified=False,
        process_accessed=False, windows_operated=False, write_permission=False,
        limitation='Static declared metadata only; dynamic properties and accessibility providers are not ruled out.',
        references=[
            'https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject_p.h',
            'https://raw.githubusercontent.com/qt/qtbase/5.15/src/corelib/kernel/qmetaobject.cpp'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    directory = (ROOT / '.local/live-acceptance').resolve()
    if not args.output.resolve().is_relative_to(directory):
        parser.error('output_must_be_local_acceptance_evidence')
    if args.output.exists():
        parser.error('existing_evidence_must_not_be_overwritten')
    report = inspect(args.dll.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
    print(json.dumps({'path': str(args.output), 'classes': [chain[0]['class'] for chain in report['class_chains']],
                      'runtime_binding_verified': False, 'write_permission': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

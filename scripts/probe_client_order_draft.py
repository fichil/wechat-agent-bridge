"""One newly operator-authorized client-order fixed draft, 2026-10-26.

Separate fixed one-use ledger, no sends/API, no quota reset or arbitrary case.
"""
import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import probe_background_draft as original


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--preflight', action='store_true')
    parser.add_argument('--operator-idle', action='store_true')
    parser.add_argument('--allow-accessibility-patch', action='store_true')
    parser.add_argument('--account')
    parser.add_argument('--group')
    parser.add_argument('--private')
    parser.add_argument('--target-evidence')
    parser.add_argument('--virtual-desktop')
    args = parser.parse_args(argv)
    if not (args.run or args.preflight):
        print(json.dumps({'state': 'disabled', 'send_enabled': False}))
        return 0
    try:
        if not args.virtual_desktop:
            raise original.ConsoleError('taskview_desktop_id_required')
        report = original.run(args, taskview_desktop_id=args.virtual_desktop, approved_case=original.CLIENT_ORDER_TRIAL)
    except Exception as exc:
        report = {'state': 'failed', 'reason': str(exc) if isinstance(exc, original.ConsoleError)
                  else 'taskview_preparation_failed', 'attempted': False, 'send_invoked': False}
    destination = original.DIRECTORY / ('client-order-draft-' + uuid.uuid4().hex + '.json')
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps({'state': report['state'], 'reason': report.get('reason'),
                      'attempted': report.get('attempted'), 'send_invoked': False,
                      'patch_restored': report.get('patch_restored'), 'evidence': str(destination)}))
    return 0 if report['state'] in ('preflight_verified', 'readback_verified') else 1


if __name__ == '__main__':
    raise SystemExit(main())

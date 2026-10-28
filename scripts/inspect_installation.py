"""Read static files only; JSON output goes to a caller-selected evidence path."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent_bridge.adapters.pe_inventory import inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve(strict=True)
    output = args.output.resolve()
    if output == root or root in output.parents:
        parser.error('output_must_not_modify_installation')
    report = {}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in ('.dll', '.exe', '.bin') or not path.is_file():
            continue
        if path.stat().st_size > 512*1024*1024:
            report[path.name] = {'status': 'size_limit'}
            continue
        with path.open('rb') as stream:
            header = stream.read(2)
            if header != b'MZ':
                report[path.name] = {'status': 'not_pe', 'size': path.stat().st_size}
                continue
            stream.seek(0)
            data = stream.read()
        try:
            report[path.name] = inventory(data)
        except ValueError as exc:
            report[path.name] = {'status': str(exc)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    for name, value in report.items():
        print(name, value.get('status', 'parsed'), 'exports='+str(len(value.get('exports', []))))


if __name__ == '__main__':
    main()

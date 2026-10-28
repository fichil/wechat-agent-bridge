"""Explicit, content-free live discovery. Does not read names or chat bodies."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent_bridge.adapters.windows import WindowsAdapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-metadata", action="store_true", required=True)
    parser.add_argument("--structure", action="store_true")
    args = parser.parse_args()
    adapter = WindowsAdapter(timeout=15)
    try:
        accounts = adapter.discover()
        result = [{"instance": a.instance_id, "runtime_version": a.version, "identity_certain": a.identity_certain} for a in accounts]
        if args.structure:
            for entry in result:
                entry["structure"] = adapter._call("structure", entry["instance"])
        print(json.dumps({"scope": "process_and_control_types_only", "adapter_revision": adapter.revision, "windows": result}, ensure_ascii=False))
    finally:
        adapter.close()


if __name__ == "__main__":
    main()

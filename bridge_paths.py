"""Shared resource/data paths, including inherited worker configuration."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Managed releases share data; standalone portable packages remain self-contained.
MANAGED_ROOT = ROOT.parent.parent if ROOT.parent.name == 'releases' and (ROOT.parent.parent / 'installation.json').is_file() else None
DEFAULT_DATA = MANAGED_ROOT / 'data' if MANAGED_ROOT else ROOT / ('data' if (ROOT / 'portable-manifest.json').is_file() else '.local')
DATA = Path(os.environ.get('BRIDGE_DATA_DIR', DEFAULT_DATA)).resolve()
FRONTEND = ROOT / 'frontend/dist'
NATIVE = ROOT / 'bin/native-send' if (ROOT / 'bin/native-send').is_dir() else DATA / 'native-send'
RECEIPTS = DATA / 'native-send-receipts'


def migration_pending():
    return (DATA / 'migration-pending.json').is_file()

"""One-shot desktop console startup, independent of the backend's child job."""
from pathlib import Path
from . import deploy as d
from .updates import Updates


def ensure_console(release):
    release = Path(release).resolve()
    updates = Updates(root=release)
    if updates.mode == 'source' or updates.manifest.get('launcher_protocol', 0) < 1:
        return True
    root = updates.root
    if (root / 'data/deployment-hold.json').exists(): return False
    directory = root if updates.mode == 'managed' else root / 'updates'
    if (directory / 'transaction.json').exists(): return False
    try:
        with d.lock(directory):
            # First tray release may have been installed by an older controller.
            current = d.read(root / 'current.json')
            if current['commit'] != updates.manifest['commit']: return True
            if (directory / 'transaction.json').exists(): return False
            if updates.mode == 'managed':
                deployment = d.Deployment(root)
                deployment.refresh_console(current['commit'])
                deployment.restore_console(current['commit'])
            else:
                d.launch_console(release, background=True)
        return True
    except PermissionError:
        # An active deployment owns this lock; retry after it releases it.
        return False

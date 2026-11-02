"""Upload one CI artifact and observe the detached server deployment task."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--request-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.commit) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', args.request_id):
        raise ValueError('Invalid deployment identity')
    host, user = os.environ['DEPLOY_HOST'], os.environ['DEPLOY_USER']
    port = str(int(os.environ.get('DEPLOY_PORT', '22')))
    if not re.fullmatch(r'[A-Za-z0-9.:-]+', host) or not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
        raise ValueError('Invalid SSH destination')
    files = list(args.artifact.glob('*.zip'))
    if len(files) != 1: raise ValueError('Expected exactly one deployment ZIP')
    archive = files[0].resolve()
    with archive.open('rb') as stream: checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    root = 'C:/Apps/WeChatAgentBridge'
    with tempfile.TemporaryDirectory(prefix='bridge-ssh-') as directory:
        key = Path(directory) / 'key'
        key.write_text(os.environ['DEPLOY_SSH_KEY'].replace('\r', '').strip() + '\n', encoding='ascii')
        key.chmod(0o600)
        hosts = Path(directory) / 'known_hosts'
        hosts.write_text(os.environ['DEPLOY_KNOWN_HOSTS'].replace('\r', '').strip() + '\n', encoding='ascii')
        options = ['-i', str(key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(hosts),
                   '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15']
        destination = user + '@' + host

        def remote(action, extra=''):
            # All interpolated values above are validated and contain no shell syntax.
            script = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; & '" + root +
                      "/controller/runtime/python.exe' -B -X utf8 '" + root + "/controller/deploy.py' " +
                      action + " --root '" + root + "' " + extra + "; exit $LASTEXITCODE")
            encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            result = subprocess.run(['ssh', *options, '-p', port, destination, 'powershell.exe', '-NoProfile',
                                     '-NonInteractive', '-EncodedCommand', encoded], capture_output=True, text=True, timeout=180)
            if result.returncode: raise RuntimeError('Remote command failed: ' + (result.stdout + result.stderr).strip())
            return json.loads(result.stdout.strip())

        remote('status')  # Fail before uploading if bootstrap is missing.
        batch = Path(directory) / 'sftp.txt'
        # POSIX OpenSSH treats C:/ as relative; Windows SFTP exposes /C:/.
        batch.write_text('put "' + archive.as_posix() + '" "/' + root + '/inbox/' + args.request_id + '.zip.part"\n', encoding='utf-8')
        subprocess.run(['sftp', *options, '-P', port, '-b', str(batch), destination], check=True, timeout=900)
        result = remote('submit', '--request-id ' + args.request_id + ' --commit ' + args.commit + ' --sha256 ' + checksum)
        deadline = time.monotonic() + 3600
        previous = None
        failures = 0
        while time.monotonic() < deadline:
            if result != previous:
                print(json.dumps(result), flush=True)
                previous = result
            if result['stage'] in ('succeeded', 'superseded'): return
            if result['stage'] in ('failed', 'rolled_back'): raise RuntimeError('Deployment did not activate; see status above')
            time.sleep(10)
            try:
                result = remote('status', '--request-id ' + args.request_id)
                failures = 0
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                failures += 1
                if failures > 12: raise
        raise TimeoutError('Deployment remains pending; server task continues independently. Check the logged-in user and status.')


if __name__ == '__main__': main()

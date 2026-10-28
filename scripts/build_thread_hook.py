"""Build the local synthetic-only DLL/executable with installed MSVC/SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]
VC = Path('C:/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools/VC/Tools/MSVC/14.29.30133')
SDK = Path('C:/Program Files (x86)/Windows Kits/10')
SDK_VERSION = '10.0.19041.0'
OUT = ROOT / '.local/thread-hook-synthetic'
BOUND_OUT = ROOT / '.local/thread-hook-bound-inspector'
SEND_OUT = ROOT / '.local/thread-hook-bound-send'


def build(*, bound_inspector=False, bound_send=False):
    bound_inspector=bound_inspector or bound_send
    output_directory = SEND_OUT if bound_send else BOUND_OUT if bound_inspector else OUT
    compiler = VC / 'bin/Hostx64/x64/cl.exe'
    if not compiler.is_file():
        raise RuntimeError('reviewed_msvc_not_installed')
    output_directory.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment['PATH'] = str(compiler.parent) + os.pathsep + environment.get('PATH', '')
    environment['INCLUDE'] = ';'.join(map(str, (VC / 'include', SDK / 'Include' / SDK_VERSION / 'ucrt',
                                               SDK / 'Include' / SDK_VERSION / 'shared', SDK / 'Include' / SDK_VERSION / 'um')))
    environment['LIB'] = ';'.join(map(str, (VC / 'lib/x64', SDK / 'Lib' / SDK_VERSION / 'ucrt/x64',
                                           SDK / 'Lib' / SDK_VERSION / 'um/x64')))
    results = []
    jobs=[('thread_hook.cpp', 'bridge_thread_hook.dll', True),
          ('thread_hook_synthetic.cpp', 'bridge_hook_synthetic.exe', False)]
    if bound_inspector: jobs.append(('bound_guard_synthetic.cpp','bridge_bound_guards.exe',False))
    for source, output, dll in jobs:
        command = [str(compiler), '/nologo', '/O2', '/W4', '/WX', '/MT', '/GS', '/guard:cf', '/std:c++17',
                   str(ROOT / 'scripts/native' / source), '/Fo' + str(output_directory / (source + '.obj')),
                   '/Fe' + str(output_directory / output)]
        if bound_inspector: command.append('/DBRIDGE_BOUND_INSPECTOR')
        if bound_send: command.append('/DBRIDGE_BOUND_SEND')
        if dll: command.append('/LD')
        command += ['/link', '/DYNAMICBASE', '/NXCOMPAT', '/guard:cf', 'user32.lib', 'kernel32.lib', 'bcrypt.lib']
        run = subprocess.run(command, cwd=output_directory, env=environment, capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(run.stdout + run.stderr)
        results.append(dict(file=output, sha256=hashlib.sha256((output_directory / output).read_bytes()).hexdigest()))
    sources = ['thread_hook.cpp', 'thread_hook_synthetic.cpp', 'thread_hook_contract.h']
    if bound_inspector: sources+=['bound_send_inspector.h','bound_guard_synthetic.cpp']
    manifest = dict(compiler=str(compiler), compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(),
                    sdk=SDK_VERSION, synthetic_only=not bound_inspector, bound_inspector=bound_inspector,
                    real_send_implemented=bound_send, binaries=results,
                    build_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    sources={name: hashlib.sha256((ROOT/'scripts/native'/name).read_bytes()).hexdigest() for name in sources})
    archive = output_directory / 'archive' / hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    if not archive.exists():
        archive.mkdir(parents=True)
        for item in results: shutil.copy2(output_directory / item['file'], archive / item['file'])
        for name in manifest['sources']: shutil.copy2(ROOT / 'scripts/native' / name, archive / name)
        shutil.copy2(Path(__file__), archive / Path(__file__).name)
        (archive / 'build.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    manifest['archive'] = str(archive)
    (output_directory / 'build.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--build-synthetic', action='store_true')
    group.add_argument('--build-bound-inspector', action='store_true')
    group.add_argument('--build-bound-send', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(bound_inspector=args.build_bound_inspector,bound_send=args.build_bound_send) if args.build_synthetic or args.build_bound_inspector or args.build_bound_send
                     else dict(state='disabled', live_enabled=False)))

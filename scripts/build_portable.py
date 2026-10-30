"""Build a clean, allowlisted Windows x64 distribution from an exact commit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.12.16'
CADDY_VERSION = '2.11.7'
CADDY_SHA512 = 'c308154504e53755958ffc62d8a2657ab17eefd6dd25c45dbb2c3d93d29519971f77ca652f7d0f2bcb7f980b9ba3822478865e29969d0272d7613550a43152b1'
DIRECTORIES = ('backend/app/', 'bridge_portable/', 'wechat_account_probe/', 'wechat_conversations/', 'wechat_messages/', 'wechat_sender/', 'native_send/')
EXACT = {'backend/__init__.py','backend/admin.py','backend/requirements.lock.txt','bridge_paths.py',
         'deploy/Caddyfile.template','scripts/production_server.py','scripts/serve_backend.py',
         'scripts/service_child.py','scripts/windows_job.py','scripts/verify_native_send_live.py',
         'scripts/observe_send_drafts.ps1'}


def digest(path, algorithm='sha256'):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,algorithm).hexdigest()


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT, **kwargs)


def native_build(stage, build):
    vswhere=Path(os.environ['ProgramFiles(x86)'])/'Microsoft Visual Studio/Installer/vswhere.exe'
    vs=Path(subprocess.check_output([str(vswhere),'-latest','-products','*','-requires',
                                    'Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath'],text=True).strip())
    vcvars=vs/'VC/Auxiliary/Build/vcvars64.bat'
    if not vcvars.exists():
        raise ValueError('MSVC x64 Build Tools are required on the build machine')
    native=stage/'bin/native-send'; native.mkdir(parents=True)
    # Static CRT also applies to the injected DLL: the remote WeChat process must
    # not depend on the launcher's DLL search path for a private VC runtime.
    commands = [
        ['cl.exe','/nologo','/std:c++17','/EHsc','/W4','/utf-8','/MT','/O2','/DNDEBUG','/Brepro',str(ROOT/'native_launcher/launcher.cpp'),
         '/Fe:'+str(stage/'WeChatAgentBridge.exe'),'/Fo:'+str(build/'launcher.obj'),'/link','/SUBSYSTEM:WINDOWS','/MACHINE:X64','/INCREMENTAL:NO','user32.lib','gdi32.lib','comdlg32.lib'],
        ['cl.exe','/nologo','/std:c++17','/EHsc','/W4','/utf-8','/LD','/MT','/O2','/DNDEBUG','/Brepro',str(ROOT/'native_send/text_bridge.cpp'),
         '/Fe:'+str(native/'bridge_text.dll'),'/Fo:'+str(build/'bridge_text.obj'),'/link','/MACHINE:X64','/INCREMENTAL:NO','bcrypt.lib'],
    ]
    script=build/'compile.cmd'
    # Compiler commands contain only paths and fixed build arguments, never user input/secrets.
    content='@echo off\ncall '+subprocess.list2cmdline([str(vcvars)])+'\nif errorlevel 1 exit /b 1\n'
    for command in commands:
        content+=subprocess.list2cmdline(command)+'\nif errorlevel 1 exit /b 1\n'
    script.write_text(content,encoding='mbcs')
    run([os.environ['COMSPEC'],'/d','/c',script])
    for suffix in ('.exp','.lib'):
        (native/('bridge_text'+suffix)).unlink(missing_ok=True)
    manifest=dict(dll_sha256=digest(native/'bridge_text.dll'),
                  sources={name:digest(ROOT/'native_send'/name) for name in ('text_bridge.cpp','core_version.h')})
    (native/'build.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',required=True)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--caddy-zip',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'.local/releases')
    parser.add_argument('--development',action='store_true',help='Local smoke build only; refuses release naming')
    args=parser.parse_args()
    import re
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?',args.version):
        raise ValueError('Use a semantic version')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    commit=subprocess.check_output(['git','rev-parse',args.commit],cwd=ROOT,text=True).strip()
    dirty=subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()
    if head!=commit or (dirty and not args.development):
        raise ValueError('Release builds require the exact clean checkout of --commit')
    if args.development and 'dev' not in args.version:
        raise ValueError('Development build version must contain dev')
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    name='WeChatAgentBridge-'+args.version+'-windows-x64'
    stage=output/name
    stage.mkdir()  # Never overwrite a previous build/data directory.
    build=output/(name+'-build'); build.mkdir()
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    if args.development:
        files+=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
    for name in sorted(set(files)):
        if name in EXACT or any(name.startswith(prefix) for prefix in DIRECTORIES):
            target=stage/name; target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/name,target)
    shutil.copytree(args.runtime.resolve(),stage/'runtime')
    (stage/'runtime/python312._pth').write_text('python312.zip\n.\nLib/site-packages\n..\n../scripts\nimport site\n',encoding='utf-8')
    run([sys.executable,'-m','pip','install','--disable-pip-version-check','--no-compile','--only-binary=:all:',
         '--target',stage/'runtime/Lib/site-packages','-r',ROOT/'backend/requirements.lock.txt'])
    run(['npm.cmd','--prefix',ROOT/'frontend','run','build'])
    shutil.copytree(ROOT/'frontend/dist',stage/'frontend/dist')
    if digest(args.caddy_zip,'sha512') != CADDY_SHA512:
        raise ValueError('Caddy archive checksum mismatch')
    (stage/'bin').mkdir(exist_ok=True)
    licenses=stage/'licenses'; licenses.mkdir()
    with zipfile.ZipFile(args.caddy_zip) as source:
        (stage/'bin/caddy.exe').write_bytes(source.read('caddy.exe'))
        for filename in ('LICENSE','NOTICE','README.md'):
            if filename in source.namelist():
                (licenses/('Caddy-'+filename)).write_bytes(source.read(filename))
    native_build(stage,build)
    shutil.copy2(ROOT/'docs/portable-windows.md',stage/'使用说明.txt')
    shutil.copy2(ROOT/'native_send/LICENSE.helper',licenses/'LICENSE.helper')
    shutil.copy2(ROOT/'native_send/NOTICE.md',licenses/'native-send-NOTICE.md')
    # Copy dependency license texts; package metadata stays with the vendored runtime.
    for path in sorted((stage/'runtime/Lib/site-packages').glob('*.dist-info')):
        for child in path.rglob('*'):
            if child.is_file() and ('license' in child.name.lower() or 'copying' in child.name.lower() or 'notice' in child.name.lower()):
                target=licenses/'python'/path.name/child.relative_to(path)
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(child,target)
    for path in (ROOT/'frontend/node_modules').rglob('package.json'):
        if '.cache' in path.parts: continue
        for child in path.parent.iterdir():
            if child.is_file() and child.name.lower().startswith(('license','copying','notice')):
                target=licenses/'frontend'/path.parent.relative_to(ROOT/'frontend/node_modules')/child.name
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(child,target)
    (stage/'THIRD_PARTY_NOTICES.txt').write_text('Python '+PYTHON_VERSION+'：runtime/LICENSE.txt 与 licenses/python\nCaddy '+CADDY_VERSION+'：licenses/Caddy-LICENSE\n网页依赖：licenses/frontend\n原生发送派生自 helper，适用非商业使用许可：licenses/LICENSE.helper 与 licenses/native-send-NOTICE.md\n',encoding='utf-8')
    manifest=dict(version=args.version,commit=commit,development=args.development,architecture='windows-x64',
                  python=PYTHON_VERSION,caddy=CADDY_VERSION,
                  files={str(path.relative_to(stage)).replace('\\','/'):digest(path) for path in sorted(stage.rglob('*')) if path.is_file()})
    (stage/'portable-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    # Run the bundled interpreter with a PATH that contains no developer tools.
    clean={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','BRIDGE_'))}
    clean['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
    run([stage/'runtime/python.exe','-B','-c',
         'import ssl,sqlite3,ctypes,fastapi,uvicorn,psutil,cryptography,zstandard,PIL,websockets,argon2; from wechat_sender.compatibility import build_identity; build_identity(); print("portable imports OK")'],env=clean)
    archive=output/(stage.name+'.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as zipout:
        for path in sorted(stage.rglob('*')):
            if path.is_file(): zipout.write(path,Path(stage.name)/path.relative_to(stage))
    checksum=output/(archive.name+'.sha256')
    checksum.write_text(digest(archive)+'  '+archive.name+'\n',encoding='ascii')
    shutil.copy2(stage/'portable-manifest.json',output/(stage.name+'.manifest.json'))
    print(json.dumps(dict(archive=str(archive),sha256=digest(archive),directory=str(stage))))


if __name__=='__main__':
    main()

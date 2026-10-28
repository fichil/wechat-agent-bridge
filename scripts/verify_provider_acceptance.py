"""Explicit paid provider acceptance with synthetic fixtures and durable quota.

Default disabled. No WeChat imports, chat reads, key creation or key output.
Uses the existing local key and fixed models already selected by the operator.
"""
import argparse
import base64
import hashlib
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import time
import uuid
import wave
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from wechat_agent_bridge.acceptance import AcceptanceLedger, AcceptanceSession, MODEL, acceptance_directory
from wechat_agent_bridge.console_models import ConsoleError
from wechat_agent_bridge.openai_config import load_openai_configuration
from wechat_agent_bridge.providers import CompatibleProvider
from wechat_agent_bridge.protection import DPAPIProtector

CAPABILITIES = ('text', 'vision', 'transcription', 'summary')
SPEECH = 'This is a synthetic speech test. The project code is bridge seven two nine.'


def synthetic_image(scale=1):
    """Known red-left / blue-right RGB fixture; no external image or library."""
    if type(scale) is not int or scale not in (1, 4):
        raise ConsoleError('invalid_synthetic_image_scale')
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    scanline = b'\0' + bytes((255, 0, 0)) * (64 * scale) + bytes((0, 0, 255)) * (64 * scale)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 128 * scale, 64 * scale, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(scanline * (64 * scale))) + chunk(b'IEND', b''))


def prepare_jpeg_control(directory):
    """Convert only our fixed synthetic PNG; Windows builtin, no GUI or network."""
    nonce=uuid.uuid4().hex;png=directory/('synthetic-format-'+nonce+'.png');jpeg=png.with_suffix('.jpg')
    source=synthetic_image(4)
    with png.open('xb') as f:f.write(source)
    try:
        result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',
            str(ROOT/'scripts/prepare_vision_jpeg.ps1'),'-SourcePng',str(png),'-DestinationJpeg',str(jpeg)],
            capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode!=0:raise ValueError()
        metadata=json.loads(result.stdout.decode('utf-8-sig'))
        if (metadata.get('source_pixels_verified')!=131072 or metadata.get('jpeg_samples_verified')!=50 or
            metadata.get('width')!=512 or metadata.get('height')!=256):raise ValueError()
        data=jpeg.read_bytes()
        if not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9') or len(data)>1000000:raise ValueError()
        metadata.update(source_sha256=hashlib.sha256(source).hexdigest(),source_path=str(png),jpeg_path=str(jpeg),
            conversion_source_sha256=hashlib.sha256((ROOT/'scripts/prepare_vision_jpeg.ps1').read_bytes()).hexdigest())
        return data,metadata
    except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired):
        raise ConsoleError('synthetic_jpeg_control_unavailable') from None


def speech_worker(pipe, path):
    stream = None
    try:
        import comtypes.client
        # Existing Windows SAPI, output redirected to a new local file, no audio
        # device playback, external voice service or downloaded voice model.
        voice = comtypes.client.CreateObject('SAPI.SpVoice', dynamic=True)
        stream = comtypes.client.CreateObject('SAPI.SpFileStream', dynamic=True)
        if Path(path).exists():
            raise ConsoleError('speech_fixture_path_exists')
        stream.Open(path, 3, False)
        voice.AudioOutputStream = stream
        voice.Speak(SPEECH, 0)
        stream.Close(); stream = None
        pipe.send({'ok': True})
    except Exception:
        pipe.send({'ok': False, 'reason': 'local_speech_fixture_unavailable'})
    finally:
        if stream is not None:
            stream.Close()
        pipe.close()


def validate_audio(data):
    if len(data) > 2 * 1024 * 1024:
        raise ConsoleError('speech_fixture_too_large')
    try:
        with wave.open(io.BytesIO(data), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
            frames = audio.readframes(audio.getnframes())
            if (audio.getcomptype() != 'NONE' or audio.getsampwidth() != 2 or
                    audio.getnchannels() != 1 or not 2 <= duration <= 20 or not any(frames)):
                raise ConsoleError('speech_fixture_invalid')
    except (wave.Error, ZeroDivisionError):
        raise ConsoleError('speech_fixture_invalid') from None
    return dict(bytes=len(data), duration_seconds=round(duration, 3), sha256=hashlib.sha256(data).hexdigest())


def prepare_audio(directory):
    path = directory / ('synthetic-speech-' + uuid.uuid4().hex + '.wav')
    context = mp.get_context('spawn')
    read, write = context.Pipe(duplex=False)
    process = context.Process(target=speech_worker, args=(write, str(path)))
    process.start(); write.close()
    try:
        if not read.poll(25):
            raise ConsoleError('local_speech_fixture_timeout')
        result = read.recv()
        process.join(2)
        if process.is_alive() or process.exitcode != 0 or result.get('ok') is not True:
            raise ConsoleError('local_speech_fixture_unavailable')
        data = path.read_bytes()
        return data, dict(path=str(path), **validate_audio(data))
    finally:
        if process.is_alive():
            process.terminate()
        process.join(2)
        read.close()
        if not process.is_alive():
            process.close()


def semantic_check(capability, value):
    if not isinstance(value, str) or not value.strip():
        return False
    folded = value.casefold()
    if capability == 'text':
        return '测试成功' in value
    if capability == 'vision':
        try:
            # Markdown wrapping is a format variation, not image evidence.
            # Only a complete, single JSON block is accepted; wrong colors and
            # extra commentary still fail. Old failed evidence stays unchanged.
            value = value.strip()
            fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', value, re.DOTALL)
            if fenced:
                value = fenced.group(1)
            return json.loads(value) == {'left_color': 'red', 'right_color': 'blue'}
        except (ValueError, TypeError):
            return False
    if capability == 'transcription':
        return 'bridge' in folded and ('729' in re.sub(r'\W+', '', folded) or 'seven two nine' in folded)
    if capability == 'summary':
        return 'bridge-729' in folded and bool(re.search(r'15[:：]00|下午\s*3\s*点|下午三点', folded))
    return False


def request(provider, ledger, capability, request_id, sample=None, capture=None, *, vision_detail='auto',sample_mime='image/png'):
    if capability not in CAPABILITIES:
        raise ConsoleError('invalid_capability')
    ledger.reserve('api', request_id)  # Persist before the provider sees a call.
    if capability == 'summary':
        value = provider.summarize('', [{'text': '合成验收资料：项目编号 BRIDGE-729，会议时间为明天下午 15:00。'}])
    elif capability == 'vision':
        value = provider.describe_image(sample, sample_mime, question=
            '识别图片左半边和右半边的颜色，只输出 JSON 对象，字段为 left_color 和 right_color，颜色使用英文小写名称。',detail=vision_detail)
    else:
        value = provider.test(capability, sample)
    if capture is not None:
        capture(capability, value)
    return dict(ok=semantic_check(capability, value),
                output_sha256=hashlib.sha256(value.encode()).hexdigest(), output_characters=len(value))


def vision_wire_transport(transport,sample,detail,record,*,sample_mime='image/png'):
    """Audit one synthetic image request; never record headers/body/key/output.

    Diagnostic only: the fixed requested snapshot must also be returned by the
    official service. A correct-looking answer from another model cannot pass.
    """
    calls=0
    def audited(path,body,content_type):
        nonlocal calls
        if calls:raise ConsoleError('vision_wire_no_automatic_retry')
        calls+=1
        try:
            payload=json.loads(body)
            messages=payload['messages'];content=messages[0]['content']
            image=content[1]['image_url'];uri=image['url']
            if (path!='/chat/completions' or content_type!='application/json' or
                payload['model']!=MODEL or payload.get('store') is not False or payload.get('stream') is not False or
                len(messages)!=1 or messages[0]['role']!='user' or len(content)!=2 or
                content[0]['type']!='text' or content[1]['type']!='image_url' or image.get('detail')!=detail or
                sample_mime not in ('image/png','image/jpeg') or
                not isinstance(uri,str) or not uri.startswith('data:'+sample_mime+';base64,') or
                base64.b64decode(uri.split(',',1)[1],validate=True)!=sample):
                raise ValueError()
        except (ValueError,KeyError,TypeError,IndexError,AttributeError):
            raise ConsoleError('vision_wire_request_mismatch') from None
        record.update(request_verified=True,image_sha256=hashlib.sha256(sample).hexdigest(),detail=detail,
            requested_model=MODEL,http_invoked=True,mime=sample_mime)
        result=transport(path,body,content_type)
        returned=result.get('model') if isinstance(result,dict) else None
        record['response_received']=True
        record['returned_model_matches']=returned==MODEL
        if returned!=MODEL:
            record['returned_model']='unexpected_or_missing'
            raise ConsoleError('vision_wire_response_model_mismatch')
        record['returned_model']=returned
        usage=result.get('usage')
        if isinstance(usage,dict):
            record['usage']={k:v for k in ('prompt_tokens','completion_tokens','total_tokens')
                if type(v:=usage.get(k)) is int and 0<=v<=100000000}
        return result
    return audited


def run(env_file, capabilities=CAPABILITIES, *, vision_fixture='original',vision_detail='auto',inspect_vision_wire=False):
    if vision_fixture not in ('original', 'large-panels-v3','jpeg-format-control-v4'):
        raise ConsoleError('invalid_vision_fixture')
    if vision_detail not in ('auto','low','high') or (inspect_vision_wire and tuple(capabilities)!=('vision',)):
        raise ConsoleError('vision_wire_requires_single_vision_capability')
    config = load_openai_configuration(env_file)
    config.update(vision_model=MODEL, transcription_model='whisper-1')
    protector = DPAPIProtector()
    if protector.unprotect(protector.protect(b'probe')) != b'probe':
        raise ConsoleError('local_protection_failed')
    directory = acceptance_directory()
    directory.mkdir(parents=True, exist_ok=True)
    ledger = AcceptanceLedger(directory / 'quota.sqlite3')
    report = dict(kind='synthetic_provider_acceptance', started=time.time(), os=platform.version(),
                  model=MODEL, vision_model=MODEL, transcription_model='whisper-1',
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  provider_source_sha256=hashlib.sha256((ROOT / 'wechat_agent_bridge/providers.py').read_bytes()).hexdigest(),
                  fixture_contract='structured-image-v2' if vision_fixture == 'original' else vision_fixture,
                  contains_real_chat=False, sends=0, results=[], before=ledger.status())
    report.update(vision_detail=vision_detail,wire_audit_enabled=inspect_vision_wire)
    nonce = uuid.uuid4().hex
    audit_path = directory / ('provider-acceptance-' + nonce + '.jsonl')
    final_path = directory / ('provider-acceptance-' + nonce + '.json')
    scale = 1 if vision_fixture == 'original' else 4
    samples = {'text': None, 'summary': None, 'vision': synthetic_image(scale)}
    report['image_fixture'] = dict(width=128 * scale, height=64 * scale,
                                   sha256=hashlib.sha256(samples['vision']).hexdigest())
    try:
        AcceptanceSession(ledger).validate_provider(config)
        sample_mime='image/png'
        if vision_fixture=='jpeg-format-control-v4':
            if tuple(capabilities)!=('vision',) or not inspect_vision_wire:raise ConsoleError('jpeg_control_requires_single_audited_vision')
            samples['vision'],report['jpeg_conversion']=prepare_jpeg_control(directory)
            sample_mime='image/jpeg';report['image_fixture']['sha256']=hashlib.sha256(samples['vision']).hexdigest()
        report['image_fixture']['mime']=sample_mime
        provider = CompatibleProvider(config)
        if inspect_vision_wire:
            report['vision_wire']=dict(request_verified=False,http_invoked=False,response_received=False)
            provider.transport=vision_wire_transport(provider.transport,samples['vision'],vision_detail,report['vision_wire'],sample_mime=sample_mime)
        if 'transcription' in capabilities:
            try:
                samples['transcription'], report['speech_fixture'] = prepare_audio(directory)
            except ConsoleError as exc:
                report['speech_fixture'] = dict(available=False, reason=str(exc))
        with audit_path.open('x', encoding='utf-8') as audit:
            for capability in capabilities:
                if capability == 'transcription' and capability not in samples:
                    report['results'].append(dict(capability=capability, ok=False, attempted=False,
                                                  reason='local_speech_fixture_unavailable'))
                    continue
                request_id = nonce + ':' + capability
                audit.write(json.dumps(dict(request_id=request_id, capability=capability, phase='before_reservation')) + '\n')
                audit.flush(); os.fsync(audit.fileno())
                before = ledger.status()['api_used']
                started = time.monotonic()
                try:
                    protected_path = directory / ('provider-output-' + nonce + '-' + capability + '.dpapi')
                    def capture(_capability, value):
                        with protected_path.open('xb') as output:
                            output.write(protector.protect(value.encode('utf-8')))
                    result = request(provider, ledger, capability, request_id, samples[capability], capture,
                        vision_detail=vision_detail,sample_mime=sample_mime)
                    result.update(capability=capability, attempted=True)
                    result['protected_output'] = str(protected_path)
                    if not result['ok']:
                        result['reason'] = 'synthetic_semantics_not_verified'
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, ConsoleError) else 'synthetic_provider_check_failed'
                    result = dict(capability=capability, ok=False, reason=reason,
                                  attempted=ledger.status()['api_used'] > before)
                result['elapsed_seconds'] = round(time.monotonic() - started, 3)
                report['results'].append(result)
                audit.write(json.dumps(result) + '\n'); audit.flush(); os.fsync(audit.fileno())
                if not result['ok']:
                    break  # No automatic retry or model/provider switching.
        report['after'] = ledger.status()
        report['passed'] = (len(report['results']) == len(capabilities) and
                            all(item['ok'] for item in report['results']))
        with final_path.open('x', encoding='utf-8') as output:
            json.dump(report, output, indent=2)
        return report, final_path
    finally:
        ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--run-synthetic', action='store_true')
    parser.add_argument('--env-file')
    parser.add_argument('--capability', action='append', choices=CAPABILITIES)
    parser.add_argument('--vision-fixture', choices=('original', 'large-panels-v3','jpeg-format-control-v4'), default='original')
    parser.add_argument('--vision-detail',choices=('auto','low','high'),default='auto')
    parser.add_argument('--inspect-vision-wire',action='store_true')
    args = parser.parse_args(argv)
    if not args.run_synthetic:
        print(json.dumps(dict(state='disabled', api_attempted=False)))
        return 0
    if not args.env_file:
        parser.error('--env-file is required for explicit existing-key reuse')
    capabilities = tuple(args.capability or CAPABILITIES)
    if len(set(capabilities)) != len(capabilities):
        parser.error('duplicate capability')
    try:
        report, path = run(args.env_file, capabilities, vision_fixture=args.vision_fixture,
            vision_detail=args.vision_detail,inspect_vision_wire=args.inspect_vision_wire)
    except Exception as exc:
        print(json.dumps(dict(state='failed', reason=str(exc) if isinstance(exc, ConsoleError) else 'provider_setup_failed')))
        return 1
    print(json.dumps(dict(evidence=str(path), passed=report['passed'], results=report['results'], quota=report['after'])))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

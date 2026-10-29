"""Explicit CLI. JSON stdout contains account fields only when --run is passed."""
import argparse
import base64
import json
from pathlib import Path
import sys

from .models import ProbeError, failure_result


def save_output(path, result):
    destination = Path(path).resolve()
    local = (Path(__file__).resolve().parents[1] / '.local').resolve()
    if not destination.is_relative_to(local):
        raise ProbeError('output_must_be_under_project_local')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects existing local evidence and unrelated data.
    with destination.open('x', encoding='utf-8') as file:
        json.dump(result, file, ensure_ascii=False, indent=2)
        file.write('\n')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='后台只读识别当前 Windows 用户全部会话中的微信账号')
    parser.add_argument('--run', action='store_true', help='显式开始真实只读扫描')
    parser.add_argument('--format', choices=('table', 'json'), default='json')
    parser.add_argument('--output', help='另存 JSON 到项目 .local 下的新文件；不会覆盖已有文件')
    args = parser.parse_args(argv)
    if not args.run:
        parser.print_help()
        return 0
    try:
        from . import discover_accounts
        result = discover_accounts()
        if args.output:
            save_output(args.output, result)
    except (ProbeError, FileExistsError) as exc:
        reason = str(exc) if isinstance(exc, ProbeError) else 'output_already_exists'
        print(json.dumps(failure_result(reason), ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps(failure_result('probe_failed')))
        return 1
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('wxid\t微信号\t微信昵称\t状态\tPID / Windows 会话\t头像状态\t头像格式\t头像字节数')
        for account in result['accounts']:
            instances = ', '.join(f"{p['pid']}/{p['session_id']}" for p in account['instances'])
            values = [account['wxid'], account['wechat_id'], account['nickname'], account['status'], instances]
            avatar = account['avatar']
            values.extend([account['avatar_status'], avatar['mime_type'] if avatar else None,
                           str(len(base64.b64decode(avatar['data_base64'], validate=True))) if avatar else None])
            # JSON escaping keeps malicious nicknames from emitting terminal controls.
            print('\t'.join(json.dumps(v, ensure_ascii=False)[1:-1] if isinstance(v, str)
                            else '未获取' for v in values))
            if account['reason']:
                print('  原因：' + account['reason'])
            if account['avatar_reason']:
                print('  头像：' + account['avatar_reason'])
        print(json.dumps(result['summary'], ensure_ascii=False))
        for issue in result['issues']:
            print(json.dumps(issue, ensure_ascii=False))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())

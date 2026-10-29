"""Private pipe worker, launched with CREATE_NO_WINDOW and IDLE priority."""
import json
import sys

from .models import Instance
from .probe import worker_response


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read(4096))
        instance = Instance(**payload['instance'])
        timeout = min(60, max(0, float(payload['timeout'])))
        result = worker_response(instance, timeout)
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        return 1  # never expose exception messages, raw memory or key material


if __name__ == '__main__':
    sys.exit(main())

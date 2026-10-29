"""Hidden child: one account binding, JSON-lines IPC, 60-second idle expiry."""
import json
import queue
import sys
import threading
import time

from wechat_account_probe.models import Instance, ProbeError
from .reader import SessionReader


def main():
    import psutil
    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS)
    incoming = queue.Queue(maxsize=2)

    def receive():
        try:
            for line in sys.stdin:
                incoming.put(json.loads(line))
        finally:
            incoming.put(None)

    threading.Thread(target=receive, daemon=True).start()
    reader = None
    messages = None
    try:
        while True:
            try:
                request = incoming.get(timeout=60)
            except queue.Empty:
                break
            if request is None:
                break
            try:
                maximum = 180 if request.get("command") in ("messages", "changes") else 60
                deadline = time.monotonic() + min(float(request.get("timeout", 60)), maximum)
                instance = Instance(**request["instance"])
                if reader is None:
                    reader = SessionReader(instance, request["wxid"])
                if reader.instance != instance or reader.wxid != request["wxid"]:
                    raise ProbeError("account_changed_during_read")
                if request["command"] == "validate":
                    reader.bind(deadline)
                    result = {"valid": True}
                elif request["command"] == "read":
                    result = reader.read(deadline)
                elif request["command"] in ("messages", "message_image", "changes"):
                    if messages is None:
                        from wechat_messages.reader import MessageReader
                        messages = MessageReader(reader)
                    method = {"messages": messages.read, "message_image": messages.image, "changes": messages.changes}[request["command"]]
                    result = method(request["data"], deadline)
                else:
                    raise ProbeError("worker_protocol_invalid")
                response = {"ok": True, "result": result}
            except ProbeError as exc:
                response = {"ok": False, "error": str(exc)}
            except FileNotFoundError:
                response = {"ok": False, "error": "conversation_database_missing"}
            except Exception:
                response = {"ok": False, "error": "conversation_read_failed"}
            print(json.dumps(response, ensure_ascii=True), flush=True)
            # Never reuse anchors or keys after a failed validation/read.
            if not response["ok"]:
                break
    finally:
        if messages:
            messages.close()
        if reader:
            reader.close()


if __name__ == "__main__":
    main()

"""Secret-safe diagnostic copies. Observers must never change business outcomes."""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import re
import time
from functools import wraps

CONTEXT = ContextVar("diagnostic_context", default=None)
PRIVATE = {"authorization", "cookie", "setcookie", "apikey", "xapikey", "xgoogapikey",
           "secret", "encryptedkey", "password", "token", "accesstoken", "refreshtoken",
           "dbkey", "dbkeys", "keyhex", "encryptionkey", "decryptionkey", "sessionkey"}
DATA_URI = re.compile(r"data:([^;,\s]+);base64,([A-Za-z0-9+/=\r\n]+)")
CREDENTIAL = re.compile(r"\b(?:sk-|xai-)[A-Za-z0-9_-]{8,}")

def image_summary(data, mime=None):
    if isinstance(data, dict) and data.get("omitted"):
        return data
    try:
        raw = data if isinstance(data, bytes) else base64.b64decode(data, validate=True)
        return dict(omitted="image_base64", mime_type=mime, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    except Exception:
        return dict(omitted="binary_data", mime_type=mime)

def sanitize(value, secrets=()):
    if isinstance(value, dict):
        mime = value.get("mime_type") or value.get("mimeType") or value.get("media_type")
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in PRIVATE or normalized.endswith("apikey"):
                result[key] = "[REDACTED]"
            elif key in ("data_base64", "_image_bytes") or (key == "data" and mime):
                result[key] = image_summary(item, mime)
            else:
                result[key] = sanitize(item, secrets)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, bytes):
        return image_summary(value)
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]").replace(json.dumps(secret)[1:-1], "[REDACTED]")
        value = CREDENTIAL.sub("[REDACTED]", value)
        # Protocols often contain another JSON document inside a text part.
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(sanitize(json.loads(value), secrets), ensure_ascii=False, separators=(",", ":"))
            except (ValueError, TypeError, RecursionError):
                pass
        return DATA_URI.sub(lambda m: json.dumps(image_summary(m[2], m[1]), separators=(",", ":")), value)
    return value

@contextmanager
def log_context(store, **metadata):
    token = CONTEXT.set((store, metadata))
    try:
        yield
    finally:
        CONTEXT.reset(token)

def diagnostic_safe(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            context = CONTEXT.get()
            store = context[0] if context else getattr(args[0], "store", None)
            if store:
                store.log_warning = "日志记录异常，部分诊断信息未保存；业务任务结果以任务记录为准。"
    return wrapped

def record_source(store, operation, inputs, result, *, aid=None, cid=None, automatic=False,
                  task_id=None, source="wechat", elapsed=0, error=None):
    try:
        details = sanitize(dict(input_parameters=inputs, result=result))
        def stable(value):
            if isinstance(value, dict):
                return {k:stable(v) for k,v in value.items() if k not in ("observed_at", "scanned_at", "updated_at", "last_synced_at", "scan_id")}
            if isinstance(value, list): return [stable(v) for v in value]
            return value
        def count(value):
            if isinstance(value, dict):
                if "account_count" in value: return value["account_count"]
                return sum(len(v) if k in ("items", "messages", "accounts") and isinstance(v,list) else count(v) for k,v in value.items())
            if isinstance(value,list): return sum(count(v) for v in value)
            return 0
        merge_key = hashlib.sha256(json.dumps([aid,cid,operation,stable(details)],sort_keys=True,ensure_ascii=False).encode()).hexdigest() if automatic and not error else None
        store.log_write(dict(category="source", operation=operation, account_id=aid, conversation_id=cid,
            task_id=task_id, trigger="auto" if automatic else "manual", status="failed" if error else "completed",
            source=source, duration_ms=int(elapsed*1000), item_count=(1 if result and ((operation=="accounts" and aid) or (operation=="image" and result.get("status")=="available")) else count(result)), error=error),details,merge_key)
    except Exception:
        store.log_warning = "后端读取日志记录异常，读取结果不受影响。"

def record_delta_groups(store, aid, inputs, result, elapsed):
    """Index changed conversation slices after their source IDs have been resolved."""
    try:
        groups = result.get("groups", [])
        if not groups:
            return
        identities = store.call(lambda: {r[0]:r[1] for r in store.db.execute(
            "SELECT source_id,conversation_id FROM conversations WHERE account_id=?", (aid,))})
        for group in groups:
            cid = identities.get(group.get("source_id"))
            if cid:
                checkpoints = [s for s in inputs if s.get("source_id") == group.get("source_id")]
                record_source(store,"changes",dict(checkpoints=checkpoints),group,aid=aid,cid=cid,automatic=True,elapsed=elapsed)
    except Exception:
        store.log_warning = "增量同步日志记录异常，同步结果不受影响。"

class Capture:
    @diagnostic_safe
    def __init__(self, profile, key, envelope, mode, url, body):
        self.key, self.store, self.log_id = key, None, None
        self.started = time.monotonic()
        self.details = {}
        self.images = [(media["data_base64"], json.dumps(image_summary(media["data_base64"], media.get("mime_type"))))
            for message in envelope.get("messages", []) if (media := message.get("content", {}).get("image")) and media.get("data_base64")]
        context = CONTEXT.get()
        if context:
            self.store, metadata = context
            self.details = sanitize(dict(input_snapshot=envelope, request_json=json.loads(body), endpoint=url,
                response_json=None, response_text=None, parsed_result=None,
                snapshot_versions={k:metadata.get(k) for k in ("memory_revision", "role_revision", "settings_revision")}), (key,))
            summary = dict(metadata, category="ai", operation=metadata.get("operation", "conversation"),
                request_id=envelope.get("request_id"), profile_id=profile.get("profile_id") or metadata.get("profile_id"),
                profile_name=profile["name"], provider=profile["provider"], model=profile["model"], protocol=profile["protocol"],
                profile_revision=profile.get("revision") or metadata.get("profile_revision"), output_mode=mode,
                status="running", application_status="running", item_count=len(envelope["messages"]))
            self.log_id = self.store.log_write(sanitize(summary, (key,)), self.details)

    def safe_response(self, value):
        value = sanitize(value, (self.key,))
        def remove_echo(item):
            if isinstance(item, dict): return {k:remove_echo(v) for k,v in item.items()}
            if isinstance(item, list): return [remove_echo(v) for v in item]
            if isinstance(item, str):
                for encoded, summary in self.images:
                    item = item.replace(encoded, summary)
            return item
        return remove_echo(value)

    @diagnostic_safe
    def response(self, response, raw, truncated=False):
        if not self.log_id:
            return
        text = raw.decode("utf-8", "replace")
        data = None
        try:
            data = self.safe_response(json.loads(text))
            self.details["response_json"] = data
        except ValueError:
            self.details["response_text"] = self.safe_response(text)
        self.details["usage"] = (data.get("usage") or data.get("usageMetadata")) if isinstance(data, dict) else None
        self.details["response_truncated"] = truncated
        self.store.log_write(dict(log_id=self.log_id, http_status=response.status_code,
            provider_request_id=sanitize(response.headers.get("x-request-id") or response.headers.get("request-id") or
                ((data.get("request_id") or data.get("responseId") or data.get("id")) if isinstance(data, dict) else None), (self.key,))), self.details)

    @diagnostic_safe
    def finish(self, result=None, error=None):
        if not self.log_id:
            return
        if result is not None:
            self.details["parsed_result"] = self.safe_response(result)
        self.store.log_write(dict(log_id=self.log_id, status="failed" if error else "completed",
            duration_ms=int((time.monotonic()-self.started)*1000), error=getattr(error, "code", None),
            message=sanitize(getattr(error, "message", None), (self.key,))), self.details)

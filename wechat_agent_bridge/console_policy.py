"""Pure authorization and target rules, independent of UI, storage and AI."""

from .console_models import ConsoleError


class ConsolePolicy:
    @staticmethod
    def validate_grant(data):
        flags = {name: data.get(name, False) for name in ("read", "upload", "reply", "paused")}
        if any(type(value) is not bool for value in flags.values()):
            raise ConsoleError("invalid_grant")
        excluded = data.get("excluded", [])
        if not isinstance(excluded, list) or len(excluded) > 1000 or any(not isinstance(x, str) for x in excluded):
            raise ConsoleError("invalid_exclusions")
        if (flags["upload"] and not flags["read"]) or (flags["reply"] and not flags["upload"]):
            raise ConsoleError("grant_prerequisites_missing")
        return flags, list(excluded)

    @staticmethod
    def require(grant, conversation, permission, *, destination, paused, automatic, epoch, expected_epoch, send_blocked):
        if not grant or not grant.read or (conversation is not None and conversation in grant.excluded):
            raise ConsoleError("read_not_authorized")
        if permission in ("upload", "reply") and (not grant.upload or grant.destination != destination):
            raise ConsoleError("upload_not_authorized_for_provider")
        if permission == "reply":
            if not grant.reply:
                raise ConsoleError("reply_not_authorized")
            if send_blocked:
                raise ConsoleError("unknown_send_requires_review")
        if automatic and (paused or grant.paused):
            raise ConsoleError("paused")
        if expected_epoch is not None and expected_epoch != epoch:
            raise ConsoleError("operation_cancelled")

    @staticmethod
    def triggers(chat, message):
        return message.own is False and not message.historical and (
            chat.kind == "private" or (chat.kind == "group" and message.mentions_self is True))

    @staticmethod
    def check_state(account, conversation, state, *, writing):
        if not isinstance(state, dict) or state.get("account_id") != account or state.get("conversation_id") != conversation:
            raise ConsoleError("target_mismatch")
        for field, reason in (("connected", "disconnected"), ("identity_certain", "identity_uncertain")):
            if state.get(field) is not True:
                raise ConsoleError(reason)
        if writing and state.get("user_active") and not state.get("isolated_send"):
            raise ConsoleError("human_takeover")
        if writing and state.get("has_draft") is not False:
            raise ConsoleError("existing_draft_or_unknown")

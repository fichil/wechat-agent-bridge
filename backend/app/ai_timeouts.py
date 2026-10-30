"""Provider response budgets shared with the graceful shutdown owner."""

DEFAULT_RESPONSE_SECONDS = 90
GROK_RESPONSE_SECONDS = 900
CONNECT_SECONDS = 10

# A read already in progress can outlast the elapsed-response check by one
# read timeout. Leave the existing source-worker cleanup allowance afterwards.
AI_SHUTDOWN_GRACE_SECONDS = 2 * GROK_RESPONSE_SECONDS + 210


def response_seconds(profile):
    return GROK_RESPONSE_SECONDS if profile.get("provider") == "grok" else DEFAULT_RESPONSE_SECONDS

"""Replaceable account-discovery boundary. No network or model dependency."""
from typing import Any, Protocol


class AccountProbe(Protocol):
    def __call__(self) -> dict[str, Any]: ...


def discover_local_accounts() -> dict[str, Any]:
    from wechat_account_probe import discover_accounts

    return discover_accounts()

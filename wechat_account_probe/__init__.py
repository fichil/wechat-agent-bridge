"""Explicit, read-only account discovery. Importing this package has no I/O."""
from .probe import discover_accounts

__all__ = ['discover_accounts']

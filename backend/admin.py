"""Local-only interactive administrator initialization and recovery."""
import argparse
from getpass import getpass
import os
import sys
from .app.auth import AuthStore, DEFAULT_AUTH_PATH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "reset-password"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--auth-database", default=os.environ.get("BRIDGE_AUTH_DATABASE", str(DEFAULT_AUTH_PATH)))
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("An interactive terminal is required; passwords must not be piped or passed as arguments")
    password = getpass("New password (4–128 characters): ")
    if password != getpass("Confirm password: "):
        parser.error("Passwords do not match")
    store = AuthStore(args.auth_database)
    try:
        store.set_password(password, username=args.username, reset=args.action == "reset-password")
        print("Administrator saved. Previous sessions have been revoked.")
    except ValueError as error:
        parser.error(str(error))
    finally:
        store.close()


if __name__ == "__main__":
    main()

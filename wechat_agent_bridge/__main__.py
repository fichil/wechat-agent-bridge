"""A fixed synthetic demo, never a live client entrypoint."""

import argparse
import json
from pathlib import Path
import sys

from .adapters.mock import MockAdapter, MockOutcome
from .application import Bridge
from .domain import ActionRequest, MessageEvent, Target
from .policy import Mode, Policy
from .state import StateStore


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "console":
        from .console import main as console_main
        return console_main(argv[1:])
    parser = argparse.ArgumentParser(description="M0 offline demo (synthetic data only)")
    parser.add_argument("--mode", choices=[mode.value for mode in Mode], default=Mode.READ_ONLY.value)
    parser.add_argument("--outcome", choices=[item.value for item in MockOutcome], default="confirmed")
    parser.add_argument("--state-db", default=".local/m0-state.sqlite3")
    parser.add_argument("--paused", action="store_true")
    args = parser.parse_args(argv)

    if args.state_db != ":memory:":
        Path(args.state_db).parent.mkdir(parents=True, exist_ok=True)
    target = Target("synthetic-account", "synthetic-conversation")
    event = MessageEvent(target, "event-1", "synthetic-peer", "Synthetic greeting")
    own = MessageEvent(target, "event-2", "synthetic-self", "Synthetic own message", True)
    adapter = MockAdapter(target, (event, event, own), MockOutcome(args.outcome))
    with StateStore(args.state_db) as state:
        bridge = Bridge(adapter, Policy(frozenset({target}), Mode(args.mode)), state)
        if args.paused:
            bridge.pause()
        events = () if args.paused else bridge.poll(target)
        action = bridge.execute(ActionRequest("demo-reply-1", target, "Synthetic reply"))
        print(json.dumps({
            "simulation_only": True,
            "mode": args.mode,
            "accepted_events": len(events),
            "status": action.status.value,
            "reason": action.reason,
            "attempted": action.attempted,
            "replayed": action.replayed,
            "mock_submissions": len(adapter.submitted),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

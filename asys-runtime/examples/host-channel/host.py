#!/usr/bin/env python3
"""The host side: follows the component's `out` stream and answers on `in`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asys_runtime.channel import Reader, Writer, direction_root  # noqa: E402

root, name = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "demo")
inbound = Reader(direction_root(root, name, "out"))
reply = Writer(direction_root(root, name, "in"))
print(f"host: following {inbound.directory}", flush=True)
for event in inbound.follow(timeout=30):
    print(f"host: received {event['type']} #{event['sequence']} {event['data']}", flush=True)
    inbound.advance(event["sequence"])
    if event["type"] == "hello":
        sent = reply.send("reply", {"to": event["sequence"], "answer": 42})
        print(f"host: sent reply as event {sent['sequence']}", flush=True)
    elif event["type"] == "done":
        print("host: component finished, exiting", flush=True)
        sys.exit(0)
print("host: timed out", flush=True)
sys.exit(1)

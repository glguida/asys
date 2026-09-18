import argparse
import json
import os
import signal
import sys
from contextlib import nullcontext
from pathlib import Path

from .config import load_config
from .files import read_json
from .queue import Queue
from .runtime import Runtime
from .channel import Reader, Writer, direction_root
from .environment import Environment, describe, environment_root, list_environments

USAGE = """Usage: asys-runtime COMMAND [OPTIONS]

  run ENVIRONMENT            Run an environment directory containing workers.json
  describe ENVIRONMENT       Show an environment's declared name and job types
  environments               List registered environments
  submit TYPE ID [ARGS...]    Publish a job; remaining arguments go to its program
  list                       List jobs
  show ID                    Show a job's request and current state
  wait ID                    Wait for a terminal outcome
  cancel ID                  Request cancellation of a job
  send CHANNEL DIRECTION TYPE   Publish one event (--data FILE or - for stdin)
  events CHANNEL DIRECTION   Print events as JSON lines (--after N, --follow,
                             --timeout SECONDS, --ack to advance the cursor)

All commands accept --root DIRECTORY (default: .asys-runtime).
submit requires --directory JOB_DIRECTORY and --workspace WORKSPACE, both prepared
by the caller. It accepts --input FILE for JSON input. Use -- before program arguments
that conflict with runtime options. Other unrecognized submit options pass through.
run also accepts a standalone runtime configuration file and --once.
Queue commands accept --environment NAME. wait accepts --timeout SECONDS.
Channel directions are in and out, named from the component's side.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"--help", "-h"}:
        print(USAGE)
        return 0
    command = argv.pop(0)
    if command not in {"run", "describe", "environments", "submit", "list", "show", "wait", "cancel", "send", "events"}:
        print(f"error: unknown command {command}", file=sys.stderr)
        return 2
    if command in {"send", "events"}:
        return channel_command(command, argv)
    parser = argparse.ArgumentParser(prog=f"asys-runtime {command}", allow_abbrev=False)
    parser.add_argument("--root", default=os.environ.get("ASYS_RUNTIME_ROOT", ".asys-runtime"))
    if command in {"run", "describe"}:
        parser.add_argument("config")
        parser.add_argument("--external", help=argparse.SUPPRESS)
    if command == "run":
        parser.add_argument("--once", action="store_true")
    elif command == "submit":
        parser.add_argument("type")
        parser.add_argument("id")
        parser.add_argument("--input")
        parser.add_argument("--directory", required=True)
        parser.add_argument("--workspace", required=True)
    elif command not in {"list", "describe", "environments"}:
        parser.add_argument("id")
        if command == "wait":
            parser.add_argument("--timeout", type=float)
    if command not in {"run", "describe", "environments"}:
        parser.add_argument("--environment")
    try:
        if command == "submit":
            options, extra = submit_arguments(parser, argv)
        else:
            options = parser.parse_intermixed_args(argv)
        if command == "describe":
            print(json.dumps(Environment(options.config, external=options.external).descriptor, indent=2))
            return 0
        if command == "environments":
            print(json.dumps(list_environments(options.root), indent=2))
            return 0
        if command == "run":
            environment = Environment(options.config, external=options.external) if Path(options.config).is_dir() else None
            if options.external is not None and environment is None:
                raise ValueError("--external requires an environment directory")
            types = environment.types if environment else load_config(options.config)
            with environment.register(options.root) if environment else nullcontext(options.root) as root:
                runtime = Runtime(root, types, health_file=os.environ.get("ASYS_RUNTIME_HEALTH_FILE"))
                previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
                for s in previous:
                    signal.signal(s, lambda *_: runtime.stop())
                try:
                    runtime.run(once=options.once)
                finally:
                    for s, handler in previous.items():
                        signal.signal(s, handler)
            return 0
        descriptor = describe(options.root, options.environment) if options.environment else None
        queue = Queue(environment_root(options.root, options.environment) if options.environment else options.root)
        if command == "submit":
            if descriptor and options.type not in descriptor["types"]:
                raise ValueError(f"environment {options.environment} does not define job type {options.type}")
            result = queue.submit(options.type, options.id, directory=options.directory, workspace=options.workspace,
                                  args=extra, input=read_json(options.input) if options.input else None)
        elif command == "list":
            result = queue.list()
        elif command == "show":
            result = {"request": queue.request(options.id), "state": queue.state(options.id)}
        elif command == "wait":
            result = queue.wait(options.id, timeout=options.timeout)
        else:
            result = getattr(queue, command)(options.id)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if command == "wait" and result["status"] != "done" else 0
    except (OSError, ValueError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def channel_command(command, argv):
    parser = argparse.ArgumentParser(prog=f"asys-runtime {command}", allow_abbrev=False)
    parser.add_argument("--root", default=os.environ.get("ASYS_RUNTIME_ROOT", ".asys-runtime"))
    parser.add_argument("channel")
    parser.add_argument("direction")
    if command == "send":
        parser.add_argument("type")
        parser.add_argument("--data", help="JSON file holding the event data; - reads stdin")
    else:
        parser.add_argument("--after", type=int, help="sequence to read past (default: the stored cursor)")
        parser.add_argument("--follow", action="store_true", help="keep printing events as they are published")
        parser.add_argument("--timeout", type=float, help="with --follow, stop after this many idle seconds")
        parser.add_argument("--ack", action="store_true", help="advance the cursor past every event printed")
    try:
        options = parser.parse_intermixed_args(argv)
        directory = direction_root(options.root, options.channel, options.direction)
        if command == "send":
            data = None
            if options.data == "-":
                data = json.load(sys.stdin)
            elif options.data:
                data = read_json(options.data)
            print(json.dumps(Writer(directory).send(options.type, data), ensure_ascii=False))
            return 0
        reader = Reader(directory)
        last = None
        try:
            events = reader.follow(options.after, timeout=options.timeout) if options.follow else reader.read(options.after)
            for event in events:
                print(json.dumps(event, ensure_ascii=False), flush=True)
                last = event["sequence"]
                if options.ack:
                    reader.advance(last)
        except KeyboardInterrupt:
            pass
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def submit_arguments(parser, argv):
    controls, positional = [], []
    iterator = iter(argv)
    for arg in iterator:
        if arg == "--":
            positional.extend(iterator)
            break
        if arg in {"--root", "--input", "--environment", "--directory", "--workspace"}:
            value = next(iterator, None)
            if value is None:
                parser.error(f"{arg} requires a value")
            controls.extend([arg, value])
        elif arg.startswith(("--root=", "--input=", "--environment=", "--directory=", "--workspace=")) or arg in {"-h", "--help"}:
            controls.append(arg)
        else:
            positional.append(arg)
    options = parser.parse_args([*controls, *positional[:2]])
    return options, positional[2:]


if __name__ == "__main__":
    raise SystemExit(main())

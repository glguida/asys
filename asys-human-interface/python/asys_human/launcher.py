"""Own a Human bridge component for the lifetime of a foreground host handler."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import uuid

from asys.lifecycle import ComponentHost, LaunchError, Interrupted
from asys.state import state_root
from asys_runtime.files import write_json
from asys_runtime.permissions import mkdir

from .channel import Channel, RPCError
from .forms import FormError
from .presentation import request_document
from .prompt import FormPrompt, Quit, Skip, TextInput, text

SERVICE = "asys.human.v1.Human"
ENDPOINT = re.compile(r"[a-z][a-z0-9-]{0,62}\.[a-z][a-z0-9-]{0,62}")


def arguments(argv):
    parser = argparse.ArgumentParser(prog="asys-human-prompt", description="Answer worker human requests, one at a time.", allow_abbrev=False)
    parser.add_argument("workers", nargs="*", metavar="COMPONENT.OUTPUT", help="Human outputs to follow (default: discover all, including new workers)")
    parser.add_argument("--system", default="asys", help="dcomp system (default: asys)")
    parser.add_argument("--claimant", default=getpass.getuser(), help="human identity for candidate checks (default: login name)")
    parser.add_argument("--root", type=Path, default=state_root("human"),
                        metavar="DIRECTORY", help="saved handler sessions")
    parser.add_argument("--dcomp-state-root", type=Path, metavar="DIRECTORY")
    parser.add_argument("--runtime-root", type=Path, metavar="DIRECTORY", help="dcomp proxy root, if nondefault")
    parser.add_argument("--once", action="store_true", help="exit after completing one answer")
    display = parser.add_mutually_exclusive_group()
    display.add_argument("--plain", action="store_true", help="use line prompts (automatic when input or output is redirected)")
    display.add_argument("--tui", action="store_true", help="force the full-screen terminal interface")
    args = parser.parse_intermixed_args(argv)
    if not args.claimant.strip() or len(args.claimant) > 256:
        parser.error("--claimant must be nonempty text of at most 256 characters")
    if any(not ENDPOINT.fullmatch(worker) for worker in args.workers) or len(set(args.workers)) != len(args.workers):
        parser.error("workers must be distinct COMPONENT.OUTPUT endpoints")
    return args


def bindings(document, selected=()):
    available = {f"{component['name']}.{output['name']}"
                 for component in document["components"] for output in component.get("outputs", [])
                 if output["service"] == SERVICE}
    if selected:
        available.intersection_update(selected)
    # Stable names prevent a queued command from moving to another worker when
    # discovery order changes or an earlier worker disappears.
    return [{"id": worker, "input": "worker-" + hashlib.sha256(worker.encode()).hexdigest()[:24]}
            for worker in sorted(available)]


class Launcher(ComponentHost):
    def __init__(self, args, interaction=None):
        super().__init__(args)
        self.interaction = interaction
        self.active = None
        self.channel = None
        self.attached = None
        self.topology = {}
        self.next_discovery = 0

    def say(self, message):
        if self.interaction:
            self.interaction.say(message)
        else:
            print(text(message), file=sys.stderr, flush=True)

    def check_interrupt(self):
        super().check_interrupt()
        if self.interaction and self.interaction.stopping.is_set():
            raise Quit()

    def view(self):
        value = self.document("view", "--json", self.args.system)
        self.topology = value
        return value

    def save(self):
        value = {"version": 1, "system": self.args.system, "component": self.name, "dcomp": self.dcomp,
                 "claimant": self.args.claimant, "workers": self.attached, "active": self.active,
                 "component_removed": not self.owned}
        path = self.directory / "session.json"
        write_json(path, value)

    def setup(self):
        root = self.args.root.expanduser().resolve()
        if "," in str(root):
            raise LaunchError("The state path cannot contain a comma (dcomp mount syntax)")
        mkdir(root, parents=True, exist_ok=True)
        session = uuid.uuid4().hex
        self.name = "human-interface-" + session[:16]
        self.directory = root / session
        mkdir(self.directory)
        self.runtime = self.directory / "runtime"
        self.definition = self.directory / "component"
        mkdir(self.definition)
        for path in [self.runtime, self.runtime / "channels", self.runtime / "channels/human",
                     self.runtime / "channels/human/in", self.runtime / "channels/human/out"]:
            path.mkdir()
            path.chmod(0o777)  # Mounted beneath a host-private session directory.
        self.channel = Channel(self.runtime, self.args.claimant, self.say)
        self.save()
        self.say(f"Human handler state: {self.directory}")
        document = self.view()
        if document.get("operation"):
            raise LaunchError(f"dcomp system {self.args.system} has a pending operation")
        image = os.environ.get("ASYS_HUMAN_INTERFACE_IMAGE") or "asys-human-interface:dev"
        try:
            self.image = self.command(["docker", "image", "inspect", "--format", "{{.Id}}", image]).strip()
        except LaunchError as error:
            raise LaunchError(f"Build the human component with make -C asys-human-interface build.\n{error}") from error
        self.attach(bindings(document, self.args.workers))
        self.next_discovery = time.monotonic() + 2

    def attach(self, workers):
        if self.owned:
            self.command(self.dcomp + ["rm-component", self.args.system, self.name])
            self.owned = []
        self.check_interrupt()
        manifest = [f"docker {self.image}"] + [f"input {SERVICE} {worker['input']}" for worker in workers]
        (self.definition / "component.dcomp").write_text("\n".join(manifest) + "\n")
        (self.runtime / "workers.json").write_text(json.dumps({"workers": workers}) + "\n")
        (self.runtime / "workers.json").chmod(0o644)
        options = ["--bind", f"{self.runtime},/var/lib/asys-human,rw",
                   "--arg=--config", "--arg=/var/lib/asys-human/workers.json"]
        for worker in workers:
            options += ["--link", f"{worker['input']}={worker['id']}"]
        self.channel.pump()
        before = self.channel.ready
        self.owned = [self.name]  # Record intent before the lifecycle operation.
        self.attached = workers
        self.save()
        self.command(self.dcomp + ["add-component", *options, self.args.system, self.name, str(self.definition)])
        deadline = time.monotonic() + 90
        next_check = 0
        while self.channel.ready <= before:
            self.check_interrupt()
            self.channel.pump()
            if time.monotonic() >= deadline:
                raise LaunchError("Timed out waiting for the human component's host channel")
            if time.monotonic() >= next_check:
                document = self.view()
                if not document.get("operation"):
                    self.check_component(document)
                next_check = time.monotonic() + 1
            self.interrupted.wait(0.1)
        self.say(f"Following {len(workers)} Human interface(s) as {self.args.claimant}. Ctrl-C to stop.")

    def check_component(self, document):
        component = next((item for item in document["components"] if item["name"] == self.name), None)
        self.check_components({self.name: component}, [self.name])

    def tick(self, cleanup=False):
        if not cleanup:
            self.check_interrupt()
        self.channel.pump()
        if self.interaction:
            self.interaction.state(self.attached or [], len(self.channel.queue))
        if cleanup or time.monotonic() < self.next_discovery:
            return
        self.next_discovery = time.monotonic() + 2
        document = self.view()
        if document.get("operation"):
            return  # Another host tool is completing a lifecycle transaction.
        self.check_component(document)
        workers = bindings(document, self.args.workers)
        if workers != self.attached:
            # Requests and their operation IDs survive the bridge restart. Also
            # refresh during an RPC, so a vanished worker cannot stall discovery.
            self.attach(workers)
        self.channel.report_unavailable(worker["id"] for worker in workers)

    def call(self, method, body, cleanup=False):
        return self.channel.call(self.active["worker"], method, body, lambda: self.tick(cleanup), timeout=5 if cleanup else 30)

    def release(self, cleanup=False):
        if self.active is None:
            return
        active = self.active
        # A signal can arrive after the worker commits a claim but before the
        # host receives its token. Repeat that exact claim to recover the token.
        if "token" not in active:
            claimed = self.call("ClaimTask", {"id": active["id"], "claimant": self.args.claimant, "claimId": active["claimId"]}, cleanup)
            active["token"] = claimed["token"]
        self.call("ReleaseTask", {"id": active["id"], "token": active["token"]}, cleanup)
        self.active = None
        self.channel.current = None
        self.save()

    def execute(self):
        stdin = None if self.interaction else TextInput()
        while True:
            self.tick()
            if not self.channel.queue:
                if stdin:
                    stdin.idle()
                self.interrupted.wait(0.1)
                continue
            key, _ = self.channel.queue.popitem(last=False)
            self.channel.current = key
            worker, task_id = key
            # Recheck queued hints: cancellation, completion and other claimants
            # may have changed the task while the preceding question was open.
            try:
                task = self.channel.call(worker, "GetTask", {"id": task_id}, self.tick)["task"]
            except RPCError as error:
                if error.code != "NotFound":
                    raise
                self.channel.current = None
                continue
            if task["status"] != "pending":
                self.channel.current = None
                continue
            self.active = {"worker": worker, "id": task_id, "claimId": uuid.uuid4().hex}
            self.save()
            try:
                claim = self.call("ClaimTask", {"id": task_id, "claimant": self.args.claimant, "claimId": self.active["claimId"]})
            except RPCError as error:
                if error.code not in {"AlreadyExists", "PermissionDenied", "FailedPrecondition", "NotFound"}:
                    raise
                self.say(f"Skipping {worker} / {task_id}: {error}")
                self.active = None
                self.channel.current = None
                self.save()
                continue
            self.active["token"] = claim["token"]
            self.save()
            completed = False
            try:
                document = request_document(worker, claim["task"], self.topology)
                temporary = self.directory / "request.tmp"
                temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
                temporary.replace(self.directory / "request.json")
                form = self.interaction.form(document) if self.interaction else FormPrompt(document)
                while True:
                    answer = form.read(self.tick if self.interaction else lambda: stdin.read(self.tick))
                    completion = {"id": task_id, "token": self.active["token"], "completionId": uuid.uuid4().hex,
                                  "resultJson": json.dumps(answer, ensure_ascii=False, allow_nan=False)}
                    self.active["completion"] = completion
                    self.save()
                    try:
                        self.call("CompleteTask", completion)
                    except RPCError as error:
                        self.say(f"Answer rejected: {error}")
                        if error.code == "InvalidArgument":
                            if self.interaction:
                                self.interaction.rejected(str(error))
                            continue
                        if error.code in {"FailedPrecondition", "NotFound", "PermissionDenied", "AlreadyExists"}:
                            self.active = None
                            self.channel.current = None
                            self.save()
                            if self.interaction:
                                self.interaction.finished("This request is no longer available.")
                            break
                        raise
                    completed = True
                    self.say("Answer recorded.")
                    break
            except Skip:
                self.channel.skipped.add(key)
                self.release()
                if self.interaction:
                    self.interaction.finished("Request skipped. It remains available to another handler.")
            except FormError as error:
                self.say(f"Cannot render this form: {error} Leaving it pending for another handler.")
                self.channel.skipped.add(key)
                self.release()
            if completed:
                self.active = None
                self.channel.current = None
                self.channel.queue.pop(key, None)
                self.save()
                if self.interaction:
                    self.interaction.finished("Answer recorded.")
                if self.args.once:
                    return

    def close(self):
        if self.directory is None:
            return True
        clean = True
        if self.active and self.owned:
            try:
                self.release(cleanup=True)
            except RPCError as error:
                if error.code not in {"FailedPrecondition", "NotFound"}:
                    self.say(f"Could not release the current claim: {error}; details are saved in {self.directory / 'session.json'}")
                    clean = False
            except Exception as error:
                self.say(f"Could not release the current claim: {error}")
                clean = False
        clean = self.cleanup_components("component.log") and clean
        self.save()
        return clean


def run_handler(launcher):
    code = 0
    try:
        launcher.setup()
        launcher.execute()
    except Interrupted:
        code = 130
    except (Quit, EOFError):
        pass
    except (LaunchError, RPCError, OSError, ValueError, KeyError) as error:
        launcher.say(f"error: {error}")
        code = 1
    finally:
        if not launcher.close() and code == 0:
            code = 1
    return code


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    fullscreen = args.tui or not args.plain and sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    interaction = None
    if fullscreen:
        try:
            from .interaction import Interaction
            from .tui import HumanApp
        except ImportError as error:
            print(f"The terminal UI is not installed: {error}. Run make -C asys-human-interface install-host.", file=sys.stderr)
            return 1
        interaction = Interaction()
    launcher = Launcher(args, interaction)
    def interrupt(*_):
        launcher.interrupted.set()
    previous = {sig: signal.signal(sig, interrupt) for sig in [signal.SIGINT, signal.SIGTERM]}
    try:
        if not fullscreen:
            return run_handler(launcher)
        app = HumanApp(interaction, lambda: run_handler(launcher), claimant=args.claimant, system=args.system)
        try:
            code = app.run() or 0
            for error in interaction.errors:
                print(text(error), file=sys.stderr)
            if interaction.errors and launcher.directory:
                print(f"Handler state: {launcher.directory}", file=sys.stderr)
            return code
        finally:
            interaction.stopping.set()
            if app.backend:
                app.backend.join()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

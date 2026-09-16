import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asys_runtime.channel import Reader, Writer, direction_root, event_path, publish_json  # noqa: E402
import asys_runtime.channel as channel


class ChannelTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="asys-channel-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.root)], check=True))
        self.out = direction_root(self.root, "demo", "out")

    def test_events_are_ordered_durable_and_read_exactly_once_per_cursor(self):
        writer = Writer(self.out)
        first = writer.send("greeting", {"hello": "host"})
        second = writer.send("greeting", ["again"])
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        reader = Reader(self.out)
        events = reader.read()
        self.assertEqual([e["data"] for e in events], [{"hello": "host"}, ["again"]])
        self.assertEqual(events[0]["type"], "greeting")
        self.assertTrue(events[0]["time"] <= events[1]["time"])
        reader.advance(events[-1]["sequence"])
        self.assertEqual(reader.read(), [])
        writer.send("greeting", None)
        self.assertEqual([e["sequence"] for e in reader.read()], [3])
        self.assertEqual(Reader(self.out).cursor, 2, "the cursor survives a reader restart")

    def test_a_restarted_writer_continues_the_sequence(self):
        Writer(self.out).send("a", 1)
        Writer(self.out).send("b", 2)
        self.assertEqual([e["type"] for e in Reader(self.out).read()], ["a", "b"])

    def test_concurrent_writers_never_collide_and_leave_no_gaps(self):
        count, per_writer = 4, 25
        def run(tag):
            writer = Writer(self.out)
            for i in range(per_writer):
                writer.send("tick", {"writer": tag, "i": i})
        threads = [threading.Thread(target=run, args=(t,)) for t in range(count)]
        for t in threads: t.start()
        for t in threads: t.join()
        events = Reader(self.out).read()
        self.assertEqual([e["sequence"] for e in events], list(range(1, count * per_writer + 1)))
        per = {}
        for e in events:
            per.setdefault(e["data"]["writer"], []).append(e["data"]["i"])
        for tag in range(count):
            self.assertEqual(per[tag], list(range(per_writer)), "each writer's own events stay in order")

    def test_a_torn_or_foreign_file_is_never_visible_as_an_event(self):
        (self.out).mkdir(parents=True)
        (self.out / ".000000001.json.tmp.1.abc").write_text('{"partial":')
        (self.out / "notes.txt").write_text("ignored")
        writer = Writer(self.out)
        self.assertEqual(writer.send("x", 0)["sequence"], 1)
        self.assertEqual(len(Reader(self.out).read()), 1)
        self.assertFalse(publish_json(event_path(self.out, 1), {"version": 1}), "publishing never overwrites")
        self.assertEqual(Reader(self.out).event(1)["type"], "x")
        (self.out / "000000002.json").write_text(json.dumps({"version": 1, "sequence": 7, "type": "x", "time": "", "data": 0}))
        with self.assertRaises(ValueError):
            Reader(self.out).read()

    def test_follow_yields_as_events_land_and_stops_on_timeout(self):
        writer = Writer(self.out)
        writer.send("first", 1)
        reader = Reader(self.out)
        received = []
        def producer():
            time.sleep(0.15)
            writer.send("second", 2)
        threading.Thread(target=producer).start()
        for event in reader.follow(timeout=0.5):
            received.append(event["type"])
        self.assertEqual(received, ["first", "second"])
        self.assertEqual(reader.cursor, 0, "follow does not acknowledge on the reader's behalf")

    def test_prune_removes_acknowledged_events_only(self):
        writer = Writer(self.out)
        for i in range(5):
            writer.send("n", i)
        reader = Reader(self.out)
        reader.advance(3)
        self.assertEqual(reader.prune(), 3)
        self.assertEqual([e["sequence"] for e in reader.read(0)], [4, 5])
        self.assertEqual(Writer(self.out).send("n", 5)["sequence"], 6, "pruning never reuses a sequence number")

    def test_pruning_everything_keeps_the_high_water_mark_for_stale_writers(self):
        stale = Writer(self.out)
        stale.send("n", 0)
        fresh = Writer(self.out)
        for i in range(1, 4):
            fresh.send("n", i)
        reader = Reader(self.out)
        reader.advance(4)
        self.assertEqual(reader.prune(), 3, "the latest event survives a full prune")
        self.assertEqual(stale.send("n", 4)["sequence"], 5)
        self.assertEqual([e["sequence"] for e in reader.read()], [5])

    def test_invalid_names_directions_and_data_are_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            direction_root(self.root, "demo", "sideways")
        with self.assertRaises(ValueError):
            direction_root(self.root, "../demo", "in")
        writer = Writer(self.out)
        with self.assertRaises(ValueError):
            writer.send("bad type!", None)
        with self.assertRaises(ValueError):
            writer.send("nan", float("nan"))
        self.assertEqual(Reader(self.out).read(), [])

    def test_pruning_cannot_hide_a_writer_paused_before_publication(self):
        for peer in ("python", "javascript"):
            with self.subTest(peer=peer):
                directory = self.root / peer
                writer = Writer(directory)
                paused, resume, consumed = threading.Event(), threading.Event(), threading.Event()
                seen, failures = [], []

                def publish(path, value):
                    if value["type"] == "delayed":
                        paused.set()
                        if not resume.wait(5):
                            raise TimeoutError("test publisher was not released")
                    return publish_json(path, value)

                def delayed():
                    try:
                        writer.send("delayed")
                    except BaseException as error:
                        failures.append(error)

                def other():
                    try:
                        if peer == "python":
                            fresh = Writer(directory)
                            fresh.send("first")
                            fresh.send("second")
                            reader = Reader(directory)
                            batch = reader.read()
                            seen.extend(e["type"] for e in batch)
                            reader.advance(batch[-1]["sequence"])
                            reader.prune()
                        else:
                            module = (Path(__file__).resolve().parents[1] / "javascript/channel.mjs").as_uri()
                            code = f'''import {{Reader, Writer}} from {json.dumps(module)};
const writer = await new Writer({json.dumps(str(directory))}).ready();
await writer.send('first'); await writer.send('second');
const reader = await new Reader(writer.directory).ready();
const events = await reader.read();
await reader.advance(events.at(-1).sequence); await reader.prune();
console.log(JSON.stringify(events.map(e => e.type)));'''
                            result = subprocess.run(["node", "--input-type=module", "-e", code],
                                                    check=True, text=True, capture_output=True, timeout=5)
                            seen.extend(json.loads(result.stdout))
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        consumed.set()

                with patch.object(channel, "publish_json", publish):
                    slow = threading.Thread(target=delayed)
                    slow.start()
                    self.assertTrue(paused.wait(3))
                    fast = threading.Thread(target=other)
                    fast.start()
                    # The unprotected publisher lets the other writer and
                    # pruner finish here; a protected one makes them wait.
                    consumed.wait(0.3)
                    resume.set()
                    slow.join(5)
                    fast.join(5)
                self.assertFalse(slow.is_alive() or fast.is_alive())
                self.assertEqual(failures, [])
                seen.extend(e["type"] for e in Reader(directory).read())
                self.assertCountEqual(seen, ["delayed", "first", "second"])


if __name__ == "__main__":
    unittest.main()

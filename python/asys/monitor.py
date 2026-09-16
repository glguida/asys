"""A terminal view over the same saved run and job records used by status."""
import sys
import time

from .runs import Runs, run_log, tail
from .transcript import JobOutput, wrap


def top(args):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("top needs a terminal; use status for plain text or JSON")
    import curses

    runs = Runs(args.root)
    output = JobOutput()
    fixed = runs.select(args.run) if args.run else None

    def display(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.keypad(True)
        screen.timeout(200)
        selected_run = selected_job = 0
        run_id = job_id = None
        focus = "jobs" if fixed else "runs"
        paused = False
        worker_output = True
        records = []
        output_key, output_lines, output_refresh = None, [], 0
        output_wrap_key, output_wrapped = None, []
        output_title = "Logs"
        output_position = None  # None follows the end; an index holds scrollback.
        problem = ""
        refresh = 0

        while True:
            now = time.monotonic()
            if not paused and now >= refresh:
                try:
                    paths = [fixed] if fixed else runs.directories()
                    records = [runs.snapshot(path) for path in paths]
                    for current in records:
                        current["log_lines"] = tail(run_log(current), 200)
                    problem = ""
                except (OSError, ValueError) as error:
                    problem = str(error)
                refresh = now + 1
            if run_id:
                selected_run = next((i for i, record in enumerate(records) if record["id"] == run_id), selected_run)
            selected_run = min(selected_run, max(0, len(records) - 1))
            record = records[selected_run] if records else None
            run_id = record["id"] if record else None
            jobs = record["jobs"] if record else []
            # Initially select the failed job, then a running job, so opening
            # an old failed run immediately shows the useful diagnostic.
            if job_id is None and jobs:
                selected_job = next((i for i, job in enumerate(jobs) if job.get("status") == "failed"),
                                    next((i for i, job in enumerate(jobs) if job.get("status") in {"running", "waiting"}), 0))
            elif job_id:
                selected_job = next((i for i, job in enumerate(jobs) if job["id"] == job_id), selected_job)
            selected_job = min(selected_job, max(0, len(jobs) - 1))
            job = jobs[selected_job] if jobs else None
            job_id = job["id"] if job else None

            screen.erase()
            height, width = screen.getmaxyx()

            def put(y, text, style=0):
                if 0 <= y < height - 1 and width > 1:
                    try:
                        text = "".join(c if c.isprintable() else " " for c in str(text))
                        screen.addnstr(y, 0, text, width - 1, style)
                    except curses.error:
                        pass  # A resize or wide character can reach the edge.

            put(0, f"asys  {time.strftime('%H:%M:%S')}  {'PAUSED' if paused else 'refresh 1s'}", curses.A_BOLD)
            put(1, f"{len(records)} runs   State: {runs.root}")
            put(2, f"Up/Down or j/k: select   Tab: runs/jobs   l: {'run log' if worker_output else 'worker output'}   Space: pause   q: quit")
            if worker_output:
                put(3, "PgUp/PgDn: scroll output   Home: beginning   End: follow latest")
            wrapped, available, output_start = [], 0, 0
            if height < 16 or width < 60:
                put(4, "Enlarge the terminal to at least 60 columns and 16 rows.")
            else:
                run_rows = min(max(1, height // 5), max(1, len(records)))
                put(4, f"{'>' if focus == 'runs' else ' '} RUN       NAME                          ENVIRONMENT   STATUS       JOBS       ELAPSED", curses.A_BOLD)
                run_start = max(0, selected_run - run_rows + 1)
                for row, current in enumerate(records[run_start:run_start + run_rows], 5):
                    selected = current["id"] == run_id
                    text = f"  {current['id'][:8]:8}  {current['name'][:28]:28}  {current.get('environment', '-')[:12]:12}  {current.get('status', 'unknown'):11}  {current['job_counts'].get('running', 0):2}/{len(current['jobs']):<5} {current['elapsed']}"
                    put(row, text, curses.A_REVERSE if selected else 0)
                if not records:
                    put(5, "No saved runs yet.")
                row = 6 + run_rows
                if record:
                    put(row, f"{record['name']}  {record['id']}  launcher: {record['launcher']}", curses.A_BOLD)
                    if record.get("error"):
                        put(row + 1, f"Error: {record['error']}")
                    elif record.get("status") == "detached":
                        put(row + 1, f"Launcher exited; last reported status: {record['reported_status']}. Components may still run.")
                    else:
                        put(row + 1, record["directory"])
                row += 3
                heading = "JOB NAME                    TYPE      STATUS        ELAPSED   DETAIL"
                put(row, f"{'>' if focus == 'jobs' else ' '} {heading}", curses.A_BOLD)
                row += 1
                job_rows = max(1, min(max(1, len(jobs)), (height - row - 4) // 2))
                job_start = max(0, selected_job - job_rows + 1)
                for offset, current in enumerate(jobs[job_start:job_start + job_rows]):
                    text = f"  {current['name'][:26]:26}  {current.get('type', '-')[:8]:8}  {current.get('status', 'unknown'):12}  {current['elapsed']:8}  {current['detail']}"
                    put(row + offset, text, curses.A_REVERSE if current["id"] == job_id else 0)
                if not jobs:
                    put(row, "No jobs have been submitted.")
                row += job_rows + 1
                if worker_output and job:
                    available = max(0, height - row - 3)
                    key = job["directory"]
                    if output_key != key:
                        output_position, output_lines, output_title = None, [], "Logs"
                    if output_key != key or (not paused and now >= output_refresh):
                        try:
                            output_title, output_lines = output.read(job)
                        except (OSError, ValueError) as error:
                            problem = f"Cannot read agent output: {error}"
                        output_key, output_refresh = key, now + 1
                    wrap_key = (id(output_lines), width)
                    if wrap_key != output_wrap_key:
                        output_wrap_key, output_wrapped = wrap_key, wrap(output_lines, width - 1)
                    wrapped = output_wrapped
                    last = max(0, len(wrapped) - available)
                    output_start = last if output_position is None else min(output_position, last)
                    position = "following" if output_position is None else f"lines {output_start + 1}-{min(output_start + available, len(wrapped))}/{len(wrapped)}"
                    put(row, f"{output_title}: {job['name']}  {job['id']}  ({position})", curses.A_BOLD)
                    for offset, line in enumerate(wrapped[output_start:output_start + available]):
                        put(row + 1 + offset, line)
                elif record:
                    put(row, "Run log", curses.A_BOLD)
                    available = max(0, height - row - 3)
                    for offset, line in enumerate(record["log_lines"][-available:] if available else []):
                        put(row + 1 + offset, line)
            if problem:
                put(height - 2, problem, curses.A_BOLD)
            screen.refresh()
            key = screen.getch()
            if key in (ord('q'), 27):
                return
            if key == ord(' '):
                paused = not paused
            elif key == ord('l'):
                worker_output = not worker_output
                selected_job, job_id = 0, None
                output_position = None
            elif worker_output and key in (curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END):
                if key == curses.KEY_HOME:
                    output_position = 0
                elif key == curses.KEY_END:
                    output_position = None
                elif key == curses.KEY_PPAGE:
                    output_position = max(0, output_start - max(1, available - 1))
                else:
                    output_position = output_start + max(1, available - 1)
                    if output_position >= max(0, len(wrapped) - available):
                        output_position = None
            elif key == ord('\t'):
                focus = "jobs" if focus == "runs" else "runs"
            elif key in (10, 13, curses.KEY_ENTER):
                focus = "jobs"
            elif key in (curses.KEY_UP, curses.KEY_DOWN, ord('j'), ord('k')):
                step = -1 if key in (curses.KEY_UP, ord('k')) else 1
                if focus == "runs":
                    selected_run = max(0, min(selected_run + step, len(records) - 1))
                    run_id = records[selected_run]["id"] if records else None
                    selected_job, job_id = 0, None
                else:
                    selected_job = max(0, min(selected_job + step, len(jobs) - 1))
                    job_id = jobs[selected_job]["id"] if jobs else None
    curses.wrapper(display)

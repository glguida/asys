# Observe runs with asys

`asys` reads saved run and job records, logs, and agent transcripts. It works
without Docker or a workflow engine, including after the run's components have
been removed. `asys-bpmn run` starts BPMN workflows; observation uses the
separate `asys` command.

```sh
asys ps
asys status latest
asys status latest --json
asys logs latest
asys logs latest -f
asys logs latest design_module --stream stderr
asys top
```

`RUN` can be a full ID, a unique ID prefix, `latest`, or a saved run directory.
`JOB` can be a job name, full ID, or unique ID prefix. A name selects all jobs
with that name; following discovers additional jobs as they appear.
`ps` lists saved runs, including completed ones. `status` and `top` show all runs
when no run is selected; `logs` defaults to the latest run.

State defaults to `$XDG_STATE_HOME/asys/runs`, or
`$HOME/.local/state/asys/runs`. `ASYS_STATE_ROOT` selects the asys base: runs
are stored in `$ASYS_STATE_ROOT/runs`. Use `asys init DIR` and source the
generated `asys-env` to select it for all asys tools. `--root DIRECTORY` on
the launcher or observer selects the runs directory directly. Options can appear
before or after arguments:

```sh
asys-bpmn run workflow.bpmn env/dummy --root ./state
asys ps --root ./state
asys top --root ./state
asys logs ./state/RUN_ID -f
```

## Logs and transcripts

Without a job selector, `logs` displays the producer's saved `run.log`. For a
BPMN run this includes named stage starts, finishes, waits, errors, and agent
progress, formatted by `asys-bpmn`. The observer displays those messages
as text. `--source events` exposes the raw event file.

Selecting a job shows its captured stdout and stderr. `--stream stdout|stderr|both`
chooses streams; `--source jobs` selects all worker logs. `--source components`
and `--source commands` show saved container and startup diagnostics. `-n N`
limits initial output to N lines per file; `-n 0 -f` follows only new output.
The default is the full run log, or 50 lines per worker log. Ctrl-C stops
following and leaves the run alone.

`top` displays runs and their jobs, refreshing once a second. Use arrows or
`j`/`k` to select, Tab to move between runs and jobs, Space to pause, and `q` to
quit. The selected agent's transcript appears below its job; `l` switches
between that output and the run log. Job details retain provider exhaustion,
retry time, and compaction status.

Transcripts include prompts, assistant messages, readable thinking supplied by
the provider, tool arguments and results, and compaction summaries. Text and
thinking appear while the response streams. PgUp/PgDn scroll, Home goes to the
beginning, and End follows the latest messages. Program jobs and agents without
a saved conversation show stdout and stderr. Encrypted reasoning cannot be
displayed as readable text.

## Saved records

Each run directory contains `run.json`, describing the instance: `id`, `name`,
`environment`, `status`, timestamps, and its `components` map. The launcher
supplies these fields. A missing name is displayed as the run ID. The observer
does not open a workflow definition to derive it.

Jobs keep `request.json` and `state.json` under
`runtime/environments/ENVIRONMENT/jobs/JOB`. The request records `directory` and
`workspace` relative to the environment queue root. The observer resolves those
references to find execution logs and workspaces. A producer can supply a job's
display name in `request.json`'s `metadata.name`; the job ID is the fallback.
Each job's execution directory and workspace appear in `status --json`.

Agents save their transcript in the job directory's `agent.json`, under
`agent.session`. It contains Pi message entries and the active `leafId`; the
observer also reads streaming text and thinking events from the job's stdout.

The observer does not interpret BPMN nodes, stage relationships, or conditions.
Another producer can use the same run records and runtime job files.

A `detached` status means the launcher exited without recording a terminal
result; components may still be running. If `run.json` supplies a `channel` path,
the observer can also read a terminal `run.result` there. It never acknowledges
channel events or changes run state. Producers can hold `launcher.lock` while
running; without this lock file, launcher liveness is unknown.

A launcher resuming a run may save `channel_after`, the last outbound sequence
from the previous execution. Only subsequent `run.result` events determine its
current status. This keeps an earlier failure visible in history without
mistaking it for the resumed run's outcome.

Install the observer alone with `make install-host` from the source root. It
requires Python 3.10 or newer; its Python package is installed with the command.

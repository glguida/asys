# Release notes

## Unreleased

- Keep the shared Human component running independently of terminal prompts.
  Workflows start it when needed; prompts attach later and can disconnect or
  reconnect without failing waiting jobs. `asys update` ensures it exists for
  running inference installations and updates it without requiring a prompt.

- Supply the workers' Human input for built-in human jobs and for `--human`,
  even when the environment omits it or declares it as an output.
  Ordinary runs use `@human_endpoint`;
  private handlers are connected only to their run and leave the global unchanged.
- Preserve Human connections across resume and report conflicting service types
  before starting work.

## 0.1.1

- `asys update` refreshes running inference services and the shared Human service
  from the installed images. Workflow and worker containers remain running.
- Resume preserves the last checkpoint when a worker or engine stops before a
  job failure is recorded. It retries unfinished stages with fresh jobs.
- `asys-bpmn run --human` and `resume --human` create a terminal handler connected
  only to that run. The handler is removed when the run ends.
- Workers send human requests to `@human_endpoint` by default.
  `asys-human-prompt` exports and binds that service. Environment manifests now
  declare `input asys.human.v1.Human human`. Upgrading from 0.1.0 also requires a
  one-time handler restart; see the [upgrade instructions](INSTALL.md#upgrade-from-010).

## 0.1.0

Initial release of asys for running agentic systems as dcomp components on a
Linux host with a local Docker Engine. Requires dcomp 0.3.1; see
[installation](INSTALL.md) for the complete requirements.

- `asys-inference` manages provider components and publishes the selected
  Provider interface as `@inference_endpoint`.
- `asys-runtime` executes typed filesystem jobs. The caller supplies a separate
  job directory and the actual project workspace.
- `asys-workers` provides named agents, programs, and human tasks. Agent
  environments supply memory, skills, tools, and extensions; a shared system
  prompt defines behavior and the final JSON result contract.
- `asys-oneshot` runs one environment agent against a workspace.
- `asys-bpmn` executes workflows in a selected environment, including branching,
  parallel work, human decisions, and error handling. Resuming a failed stage
  creates a new job while preserving the project workspace and completed work.
- `asys` provides run listings, status, logs, agent transcripts, and a terminal
  monitor shared by the launchers.
- Human requests pass through typed component interfaces and a filesystem host
  channel to `asys-human-prompt`.
- Workflow and worker containers run with the invoking user's UID/GID. New
  starts and resumes use current images; running containers retain their images.

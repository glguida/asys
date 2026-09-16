# Release notes

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

### Known limitation

If the host launcher detects a component failure before the BPMN engine has
processed the job failure, cleanup can record the run as cancelled without a
resumable failure checkpoint. Such a run cannot be resumed with
`asys-bpmn resume`. Normal job failures use the BPMN error and resume paths.

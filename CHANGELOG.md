# Release notes

## Unreleased

- Replace BPMN loop and ad-hoc activity instance patches with registered loop
  and sequence-flow constructors. Keep ad-hoc work idle until selected, including
  after recovery, and prevent discards from cascading into other selections.
  Discarded active selections now report failure to their coordinator.
  Gateways and events between selections retain native execution and recovery.
  Reject unconnected ad-hoc compensation handlers before execution so they
  cannot run automatically on scope entry.
- Route explicit FEEL conditional-event scripts through the engine's script
  registry. Pin engine/serializer assumptions with contract tests and add an
  advisory CI check against their latest releases.
- Change the workflow engine identity to `bpmn-elements@17.3.0+asys.2`.
  Checkpoints from the previous adapter cannot be recovered or resumed by this
  version, including failed runs from 0.1.2. Finish or resume those runs with
  the previous installation before upgrading, or start a new run. Saved logs,
  results, and workspace files remain available.

## 0.1.2

- Add the reusable `asys-goal` worker and host launcher. Fresh implementation
  and verification sessions repeat until the goal is verified, with no default
  attempt limit. Both phases can ask a human for help; evidence, lessons, and
  replies remain in job state. BPMN can use the same worker as an ordinary job.
- Supply the built-in `simple` agent independently of worker environments.
  One-shot now takes an environment and prompt, without an agent argument.
  `asys system-model set/list` configures defaults, and `--model` overrides
  the model for a one-shot or goal run. See the
  [upgrade instructions](INSTALL.md#upgrade-from-011).
- Load stable `prompt.md` instructions for named environment agents alongside
  their memory and resources, and record the effective definition in job state.
- Install a portable asys Agent Skill covering team and environment design,
  BPMN, one-shot, goals, human decisions, and recovery, with runnable templates.
  `asys skill` locates it; `asys skill DEST` copies it for any compatible agent.
- Teach agents to prepare human briefings with completed work, evidence,
  concrete questions, and the consequences of each choice.

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

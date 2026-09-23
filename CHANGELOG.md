# Release notes

## Unreleased

- Add `asys-swarm` as one worker job, with population state and the turn algorithm
  inside workers. Worlds run as independent host programs or components using a
  versioned protocol over runtime channels. Swarm configuration carries data;
  workers do not import world modules. Runs retain bounded execution, checkpoints,
  member transcripts, events and deterministic replay data.
- Add an action-only `asys-swarm-agent` worker and the editable Rainkeepers
  terrarium example, with scripted and model environments and a live browser
  view. The example measures whether constructions keep gardens alive through
  a drought after every agent has been removed.
- Document per-agent observations, world services, and local or shared
  information policies through the runtime and world interfaces.

## 0.1.5

- Strengthen goal prompts to finish actionable implementation before handing off
  and critically review implementation and test adequacy during verification.
  Focus checks on unresolved risks and avoid broad reruns when a known gap
  already prevents acceptance.
- Give malformed agent completion JSON one format correction in the same session,
  preserving completed work and observations. Include the parser diagnostic and
  show complete JSON examples in goal prompts, so a formatting mistake can be
  corrected before it fails a goal or another agent job.

## 0.1.4

- Define and independently review success criteria before goal implementation.
  Preserve accepted contracts, review feedback and unresolved findings across
  attempts; reopen the contract when implementation or verification finds a
  material gap. Any phase can ask for human help. Keep unlimited attempts by
  default and give malformed phase reports one opportunity for correction.

## 0.1.3

- Present Human requests with the question, work summary, and evidence links
  first. Keep request identifiers and technical details in a separate tab or
  `/details` in plain mode, while preserving response drafts during navigation.
- Open workspace file links in the terminal preview, resolve links relative to
  the document, and support returning to the previous file. Expand the authoring
  guide with concrete instructions for preparing human decisions and evidence.
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

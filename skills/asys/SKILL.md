---
name: asys
description: Use asys to create and run agent teams, worker environments, BPMN workflows, one-shot assignments, and implementation/verification goal loops. Covers workers.json, role prompts, skills and tools, FEEL bindings, human decisions, inference setup, model selection, run inspection, and recovery. Use when authoring or operating an asys project; not for generic BPMN or unrelated agent frameworks.
---

# Build and operate asys projects

Use this skill to turn a requested outcome into executable assignments, file
contracts, and verified results with asys. Keep the user's actual scope: running
one assignment does not require designing a team, and authoring a workflow does
not require starting paid inference or running it against production files.

## Choose the execution shape

| Need | Use |
| --- | --- |
| One assignment with the built-in `simple` agent | `asys-oneshot ENVIRONMENT "assignment"` |
| Work until independently verified, with human help for blockers | `asys-goal ENVIRONMENT "goal"` |
| Specialized roles, branching, parallel work, programs, or explicit human decisions | `asys-bpmn run workflow.bpmn ENVIRONMENT --input request.md` |
| A goal inside a larger workflow | A normal BPMN task bound to the `goal` worker type |

An **agent** is a definition: stable instructions, optional retained memory, and
resources. Its model is a separate selection. An **environment** packages the
programs, tools, skills, and normally its named agents. `workers.json` maps job
types to command vectors. A **workflow** supplies assignments and sequencing.
The **workspace** contains the project files; **run state** contains execution
records. Keep these responsibilities distinct when choosing where to put code
or instructions.

## Work through the design

1. Inspect the existing workflow, environment, workspace, and installed command
   help. Reuse the project's conventions and job types when they fit.
2. Express the requested outcome as observable deliverables and acceptance
   criteria. Name workspace paths, commands that check them, and any decision
   the human actually needs to make.
3. Select the simplest execution shape above. For a team, assign concrete
   responsibilities and file ownership to roles. Decide which prerequisites
   enable each role and what its result must contain.
4. Build the environment with the required programs, role prompts, skills,
   models, and component inputs. Keep task-specific instructions in the
   assignment and reusable tool knowledge in skills.
5. Bind BPMN tasks to declared job types. Define exact JSON result fields before
   writing conditions that consume them. Include repair paths when requested;
   treat review rejection and execution failure separately.
6. Exercise file handoff, conditions, and errors with deterministic programs in
   a disposable workspace. Then validate the real toolchain and model behavior
   to the extent required by the task. A fixture run proves only its exercised
   orchestration, not the real agents' correctness.
7. Deliver the project files, exact launch command, model/setup requirements,
   checks performed, and any remaining limitation. For a run, include its saved
   ID or state path and where to inspect the artifacts.

## Read the relevant references

Load the pages needed for the current task rather than the entire manual.

| Task | Reference |
| --- | --- |
| Install/distribute this skill, initialize state, connect inference, select models, find CLI options | [Setup and commands](references/setup-and-commands.md) |
| Build a Docker environment, define a team, write prompts/memory/skills/extensions, implement programs | [Environments and teams](references/environments-and-teams.md) |
| Write BPMN, FEEL, bindings, gateways, results, diagrams, error handling | [BPMN authoring](references/bpmn.md) |
| Multi-instance work, ad-hoc coordinators, data mappings, messages and component APIs | [Advanced workflows](references/advanced-workflows.md) |
| Use one-shot or goal, understand verification, escalation, limits and lessons | [One-shot and goal](references/oneshot-and-goal.md) |
| Prepare intelligible human requests, forms, evidence and decision branches | [Human decisions](references/human-decisions.md) |
| Observe runs, diagnose failures, resume BPMN, understand queues and channels | [Operations and recovery](references/operations.md) |

The [team starter](assets/team/workflow.bpmn) includes a displayable BPMN diagram,
an implementer and reviewer with their own prompts, a deterministic artifact
check, and interchangeable `env/dummy` and `env/agents` environments.
Follow the copy/run instructions in the environments reference. The
[goal starter](assets/goal.bpmn) shows the same goal worker as a BPMN task.
All references and assets travel with this skill; no asys source checkout is
needed to use them.

## Preserve the execution contracts

- Jobs edit the selected workspace directly. Asys does not create isolated
  worktrees or copy artifacts between stages. Serialize changes to shared files
  or explicitly arrange independent workspaces. Failed jobs can leave partial
  changes.
- `--input FILE` on BPMN supplies the file's literal Markdown text as `request`.
  It is not a JSON variables loader. BPMN conditions and bindings use FEEL,
  not JavaScript or shell interpolation.
- An agent finishes with a JSON object containing nonempty `final` and
  `exception: null` or a nonempty blocker. Ask explicitly for additional fields
  consumed by the workflow. `approved: false` is a successful review requesting
  revision; a non-null `exception` fails the job.
- Program stdout is a log. Write structured output to `ASYS_RESULT`; return a
  nonzero exit status when execution failed. Commands are argument vectors;
  use an explicit shell only for shell syntax.
- `simple` is supplied by asys. One-shot takes no agent positional argument;
  one-shot and goal use `asys system-model set simple MODEL` or `--model MODEL`.
  Ordinary role jobs select their agent and model in `workers.json`.
- Goals first define and review success criteria, then implement and verify
  with no default attempt limit. Each phase uses a fresh session and can request
  human help. Criteria and unresolved findings persist between attempts.
  Evidence comes from artifacts and executed checks, not completion claims.
- Agents can propose lessons in job state. Ordinary execution does not rewrite
  retained agent memory. Promoting lessons into an environment is an explicit
  authoring change.
- A human request's producer must explain the completed work, evidence,
  question, and consequences. A generic question plus a state dump cannot
  become a useful briefing through display formatting.
- Validate semantics with actual execution and domain checks. Parseable XML,
  declared job types, a clean agent report, and a passing narrow check each
  establish different things.

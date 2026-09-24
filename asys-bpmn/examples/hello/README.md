# Hello workflow

This complete workflow runs a Python program through the ordinary job contract.
It writes `{"message":"Hello from asys."}` to `ASYS_RESULT`; the workflow returns
that value under `greet`. No inference provider or request file is required.

From this directory after installation:

```sh
asys-run env/dummy workflow.bpmn
```

From the repository root after building the images:

```sh
tools/asys-run asys-bpmn/examples/hello/env/dummy asys-bpmn/examples/hello/workflow.bpmn
```

The environment supplies `workers.json`, Dockerfile and component manifest.
The runner builds its image automatically. The BPMN contains both the process
and diagram interchange coordinates; the dashboard lays out actual sequence
flows independently of those coordinates. See the [authoring skill](../../../skills/asys-authoring/SKILL.md)
for creating another environment or replacing the program with a named worker.

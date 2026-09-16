# Shared workspace example

Four program jobs prepare a project, write two sections in parallel, and assemble
the report after both sections finish. They work directly in one supplied
workspace. No inference provider is required.

From the repository root:

```sh
make -C asys-bpmn build
mkdir -p /tmp/report-project
asys-bpmn/bin/asys-bpmn run \
  asys-bpmn/examples/shared-workspace/workflow.bpmn \
  asys-bpmn/examples/shared-workspace/env/dummy \
  --workspace /tmp/report-project \
  --input asys-bpmn/examples/shared-workspace/request.md
```

The report is `/tmp/report-project/project/report.txt`. Execution logs and
results remain in the run state directory printed by the launcher. The BPMN
includes diagram interchange data and can be opened in bpmn.io.

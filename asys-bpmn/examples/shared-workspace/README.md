# Shared workspace

Four program jobs prepare a project, write two sections in parallel, then join
to assemble a report. Each job receives the same actual workspace and its own
evidence directory. Concurrent writers use separate output paths. No model is
required.

From the repository root after building the images:

```sh
mkdir -p /tmp/report-project
tools/asys-run asys-bpmn/examples/shared-workspace/env/dummy \
  asys-bpmn/examples/shared-workspace/workflow.bpmn \
  --workspace /tmp/report-project \
  --input asys-bpmn/examples/shared-workspace/request.md
```

The deliverable is `/tmp/report-project/project/report.txt`. Inspect the printed
run ID with `tools/asys status RUN` or `tools/asys dashboard`. Logs and per-job
results remain in run state; project deliverables stay in the workspace.

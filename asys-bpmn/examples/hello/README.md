# Hello workflow

A complete workflow example with its own worker environment:

```text
hello/
  workflow.bpmn
  env/
    dummy/
      workers.json
      Dockerfile
      component.dcomp
      programs/agent
```

Open [workflow.bpmn](workflow.bpmn) in [bpmn.io](https://demo.bpmn.io/) to view
and edit the diagram. The same file contains the executable process and its
diagram layout: start → write a greeting → end.

From this directory, run:

```sh
asys-bpmn run workflow.bpmn env/dummy
```

In a source checkout, the built executable is `../../bin/asys-bpmn`:

```sh
../../bin/asys-bpmn run workflow.bpmn env/dummy
```

Build asys once using the [setup instructions](../../README.md#run-a-workflow).
The command builds this example's environment automatically. This example needs
no inference server or workflow input file.

The workflow executes a Python script through the environment's `program` type.
The script is in `workflow.bpmn`; `env/dummy/workers.json` defines its runner,
and the Dockerfile supplies Python through the shared `asys-workers` base image.
The expected output is:

```json
{
  "greet": {
    "message": "Hello from asys."
  }
}
```

The environment also provides an `agent` type that echoes its prompt and a
`human` type for requests through the Human interface. The hello workflow uses
only `program`. The example directory can live anywhere; the launcher takes
both paths explicitly.

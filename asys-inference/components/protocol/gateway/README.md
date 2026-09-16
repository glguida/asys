# Gateway implementation schemas

These protobuf messages remain an internal vocabulary for the gateway's
service implementation. They are not declared in `gateway/component.conf`,
are not served as DComp interfaces, and are not called by the Cyclo host. The
component-owned host contract is the JSON REST API documented in
`docs/component-http-api.md`.

Within the implementation, `cyclo.gateway.v1.Discovery` describes the
authentication-provider catalogue, `cyclo.gateway.v1.Admin` describes
credential-changing operations, and `cyclo.gateway.v1.Usage` describes the
read-only audit query.

`Admin.Login` is a unary operation whose lifetime scopes an interactive
exchange over the gateway component's attached standard input and output.
Its `interactive` flag controls whether API-key stdin is prompted as a terminal
or consumed silently as one line; the secret is never an RPC field.
`Admin.Logout` and `Admin.Rename` are ordinary unary calls.

`npm run generate` produces JavaScript bindings under `gen/` and the historical
checked-in Python bindings under `src/cyclo/gateway/v1/`. Production host code
does not import those Python Connect clients. The generators and their BSR
revisions remain pinned in `buf.gen.python.yaml`.

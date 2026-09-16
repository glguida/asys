import { createConnectTransport } from "@connectrpc/connect-node";
import { inputHttpOptions } from "@dcomp/component";

/** Create a ConnectRPC transport for one DComp 0.2 input socket. */
export function createDCompTransport(name, env = process.env) {
  return createConnectTransport({
    baseUrl: "http://dcomp",
    httpVersion: "1.1",
    nodeOptions: inputHttpOptions(name, env),
  });
}

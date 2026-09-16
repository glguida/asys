import { createConnectTransport } from "@connectrpc/connect-node";

export function createTestTransport(port) {
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new TypeError("test server port must be between 1 and 65535");
  }
  return createConnectTransport({
    baseUrl: `http://127.0.0.1:${port}`,
    httpVersion: "1.1",
  });
}

export async function listenTestServer(
  server,
  { host = "127.0.0.1", port = 0 } = {},
) {
  if (typeof host !== "string" || !host) {
    throw new TypeError("test server host must be a non-empty string");
  }
  if (!Number.isSafeInteger(port) || port < 0 || port > 65_535) {
    throw new TypeError("test server port must be between 0 and 65535");
  }
  await new Promise((resolve, reject) => {
    const onError = (error) => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = () => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, host);
  });
  return server.address();
}

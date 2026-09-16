import { createClient } from "@connectrpc/connect";
import { createDCompTransport } from "@cyclo/component/transport";
import { Provider } from "@cyclo/provider/contract";

export function createUpstreamBinding({ env = process.env } = {}) {
  const client = createClient(
    Provider,
    createDCompTransport("upstream", env),
  );
  return Object.freeze({
    client,
    callOptions(signal, timeoutMs) {
      const options = { signal };
      if (timeoutMs !== undefined) options.timeoutMs = timeoutMs;
      return options;
    },
  });
}

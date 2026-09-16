import { Code, ConnectError } from "@connectrpc/connect";
import { HealthStatus } from "@cyclo/component/contract";
import {
  HostAPIError,
  createHostAPIServer,
  methodNotAllowed,
  notFound,
  readJSON,
  requestSignal,
  requestURL,
  sendJSON,
} from "@cyclo/component/http";
import { AuthenticationMethod } from "@cyclo/gateway-protocol/contract";
import { providerCatalogueDocument } from "@cyclo/provider/http";

const AUTHENTICATION = Object.freeze({
  auto: AuthenticationMethod.UNSPECIFIED,
  oauth: AuthenticationMethod.OAUTH,
  api_key: AuthenticationMethod.API_KEY,
});

export function createGatewayHTTPServer({ services, shutdownSignal, healthOnly = false } = {}) {
  requireServices(services);
  return createHostAPIServer({
    shutdownSignal,
    async handler(request, response) {
      const url = requestURL(request);
      const context = {
        signal: requestSignal(request, response, shutdownSignal),
      };

      if (url.pathname === "/health") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        const health = await services.component.health({}, context);
        const ready = health?.status === HealthStatus.READY;
        sendJSON(response, ready ? 200 : 503, {
          status: ready ? "ready" : "not_ready",
          message: safeMessage(health?.message),
        });
        return;
      }
      if (healthOnly) return notFound(url.pathname);
      if (url.pathname === "/v1/models") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        const catalogue = await services.provider.listModels({}, context);
        sendJSON(response, 200, providerCatalogueDocument(catalogue));
        return;
      }
      if (url.pathname === "/v1/providers") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        const result = await services.discovery.listProviders({}, context);
        sendJSON(response, 200, {
          providers: (result?.providers ?? []).map((provider) => ({
            id: provider.id,
            description: provider.description,
            oauth: provider.oauth,
            api_key: provider.apiKey,
          })),
        });
        return;
      }
      if (url.pathname === "/v1/usage") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        sendJSON(response, 200, usageDocument(
          await services.usage.getUsage({}, context),
        ));
        return;
      }
      if (url.pathname === "/v1/login") {
        if (request.method !== "POST") return methodNotAllowed(response, ["POST"]);
        const document = await readJSON(request, { signal: context.signal });
        exactFields(document, ["provider", "account", "authentication", "interactive"]);
        const authentication = AUTHENTICATION[requiredString(
          document.authentication,
          "authentication",
        )];
        if (authentication === undefined) {
          throw new HostAPIError("authentication must be auto, oauth, or api_key");
        }
        const result = await services.admin.login({
          provider: requiredString(document.provider, "provider"),
          account: optionalString(document.account, "account"),
          authentication,
          interactive: requiredBoolean(document.interactive, "interactive"),
        }, context);
        sendJSON(response, 200, {
          account: result.account,
          authentication: authenticationName(result.authentication),
        });
        return;
      }
      if (url.pathname === "/v1/logout") {
        if (request.method !== "POST") return methodNotAllowed(response, ["POST"]);
        const document = await readJSON(request, { signal: context.signal });
        exactFields(document, ["account"]);
        const result = await services.admin.logout({
          account: requiredString(document.account, "account"),
        }, context);
        sendJSON(response, 200, { account: result.account });
        return;
      }
      if (url.pathname === "/v1/rename") {
        if (request.method !== "POST") return methodNotAllowed(response, ["POST"]);
        const document = await readJSON(request, { signal: context.signal });
        exactFields(document, ["account", "new_account"]);
        const result = await services.admin.rename({
          account: requiredString(document.account, "account"),
          newAccount: requiredString(document.new_account, "new_account"),
        }, context);
        sendJSON(response, 200, {
          account: result.account,
          new_account: result.newAccount,
        });
        return;
      }
      return notFound(url.pathname);
    },
  });
}

function requireServices(services) {
  for (const [group, methods] of Object.entries({
    component: ["health"],
    provider: ["listModels"],
    discovery: ["listProviders"],
    usage: ["getUsage"],
    admin: ["login", "logout", "rename"],
  })) {
    for (const method of methods) {
      if (typeof services?.[group]?.[method] !== "function") {
        throw new TypeError(`gateway ${group}.${method} implementation is required`);
      }
    }
  }
}

function exactFields(document, fields) {
  const expected = new Set(fields);
  const unknown = Object.keys(document).filter((field) => !expected.has(field));
  if (unknown.length) {
    throw new HostAPIError(`unknown request field: ${unknown[0]}`);
  }
}

function requiredString(value, field) {
  if (typeof value !== "string" || !value) {
    throw new HostAPIError(`${field} must be a non-empty string`);
  }
  return value;
}

function optionalString(value, field) {
  if (value === undefined) return "";
  if (typeof value !== "string") {
    throw new HostAPIError(`${field} must be a string`);
  }
  return value;
}

function requiredBoolean(value, field) {
  if (typeof value !== "boolean") {
    throw new HostAPIError(`${field} must be a boolean`);
  }
  return value;
}

function authenticationName(value) {
  if (value === AuthenticationMethod.OAUTH) return "oauth";
  if (value === AuthenticationMethod.API_KEY) return "api_key";
  throw new ConnectError("gateway returned no login authentication", Code.Internal);
}

export function usageDocument(value) {
  return {
    version: value?.version,
    totals: counterDocument(value?.totals),
    by_provider: counterMap(value?.byProvider),
    by_model: counterMap(value?.byModel),
  };
}

function counterMap(value) {
  return Object.fromEntries(
    Object.entries(value ?? {}).map(([name, counters]) => [name, counterDocument(counters)]),
  );
}

function counterDocument(value) {
  return {
    requests: decimal(value?.requests),
    latency_ms: decimal(value?.latencyMs),
    input_tokens: decimal(value?.inputTokens),
    output_tokens: decimal(value?.outputTokens),
    total_tokens: decimal(value?.totalTokens),
    cached_input_tokens: decimal(value?.cachedInputTokens),
    reasoning_tokens: decimal(value?.reasoningTokens),
    outcomes: Object.fromEntries(
      Object.entries(value?.outcomes ?? {}).map(([name, count]) => [name, decimal(count)]),
    ),
  };
}

function decimal(value) {
  if (value === undefined) return "0";
  if (typeof value === "bigint" && value >= 0n) return value.toString();
  if (Number.isSafeInteger(value) && value >= 0) return String(value);
  throw new ConnectError("gateway returned an invalid usage counter", Code.Internal);
}

function safeMessage(value) {
  return typeof value === "string"
    ? value.replace(/[\u0000-\u001f\u007f]/gu, " ").slice(0, 512)
    : "";
}

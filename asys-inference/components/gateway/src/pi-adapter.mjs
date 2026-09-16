import { Code, ConnectError } from "@connectrpc/connect";
import { STATUS_CODES } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { createResourceExhaustedError } from "@cyclo/provider/errors";
import { decodePayload, encodePayload } from "@cyclo/provider/protocol";

import { credentialSecretValues } from "./credentials.mjs";

const DEFAULT_EXHAUSTION_RETRY_MS = 60_000;
const TRANSIENT_HTTP = new Set([408, 500, 502, 503, 504, 529]);
const MAX_RETRIES = 3;

class GatewayTransientError extends Error {
  constructor(status) {
    super(`Upstream HTTP ${status} ${STATUS_CODES[status] ?? 'Overloaded'}`);
    this.status = status;
  }
}

class GatewayResponseError extends Error {}
export class GatewayCredentialError extends Error {}
class GatewayResourceExhaustion extends Error {
  constructor(retryAt) {
    super("provider resource exhausted");
    this.retryAt = retryAt;
  }
}

// The gateway terminates the opaque transport because this is where a Pi call
// becomes a native provider request. It does not interpret messages, tools,
// schemas, arguments, or successful model output. It owns bounded retries
// before output begins and enforces its credential boundary on serialized events.
export function createPiAdapter({
  now = Date.now,
  sleep = (ms, signal) => delay(ms, undefined, { signal }),
} = {}) {
  return Object.freeze({
    infer(route, payload, signal) {
      return dispatch(route, payload, signal, now, sleep);
    },
  });
}

async function* dispatch(route, payload, signal, now, sleep) {
  for (let retry = 0; ; retry++) {
    try {
      signal?.throwIfAborted();
      yield* dispatchAttempt(route, payload, signal, now);
      return;
    } catch (error) {
      if (signal?.aborted) throw upstreamFailure(error, signal);
      if (!(error instanceof GatewayTransientError)) throw error;
      if (retry === MAX_RETRIES) {
        throw new ConnectError(`${error.message} after ${retry + 1} attempts`, Code.Unavailable);
      }
      const delayMs = 1000 * 2 ** retry;
      console.error(`${error.message}; retry ${retry + 1}/${MAX_RETRIES} in ${delayMs} ms`);
      try { await sleep(delayMs, signal); }
      catch (error) { throw upstreamFailure(error, signal); }
    }
  }
}

async function* dispatchAttempt(route, payload, signal, now) {
  if (
    !route?.models
    || typeof route.models.streamSimple !== "function"
    || !route.credentialStore
    || typeof route.credentialStore.read !== "function"
  ) {
    throw new ConnectError("model adapter is unavailable", Code.Unavailable);
  }

  const frame = piCallFrame(payload);
  const localAbort = new AbortController();
  const dispatchSignal = signal
    ? AbortSignal.any([signal, localAbort.signal])
    : localAbort.signal;

  try {
    const api = route.rawModel.api;
    let credential;
    try {
      credential = await route.credentialStore.read(route.provider, {
        signal: dispatchSignal,
      });
    } catch (error) {
      if (dispatchSignal.aborted) throw error;
      throw new GatewayCredentialError("gateway credential unavailable", {
        cause: error,
      });
    }
    const reflectionGuard = credentialReflectionGuard(
      credentialSecretValues(credential),
    );
    let response;
    const options = gatewayOptions(
      frame.options,
      dispatchSignal,
      api,
      (value) => { response = value; },
      (headers) => {
        reflectionGuard.add(Object.values(headers));
        return headers;
      },
    );
    const native = route.models.streamSimple(route.rawModel, frame.context, options);
    let emitted = false;

    for await (const event of native) {
      let encoded;
      try {
        encoded = encodePayload(event);
      } catch {
        throw new GatewayResponseError(
          "upstream emitted a non-JSON Pi event",
        );
      }
      reflectionGuard.check(encoded);

      if (!emitted) {
        const retryAt = providerExhaustionRetryAt(api, response, event, now);
        if (retryAt !== undefined) {
          throw new GatewayResourceExhaustion(retryAt);
        }
        if (event.type === "error" && TRANSIENT_HTTP.has(response?.status)) {
          throw new GatewayTransientError(response.status);
        }
      }

      emitted = true;
      yield {
        payload: encoded,
        usage: eventUsage(event),
      };
    }
  } catch (error) {
    if (error instanceof GatewayCredentialError) throw error;
    if (error instanceof GatewayTransientError) throw error;
    if (error instanceof GatewayResourceExhaustion) {
      throw createResourceExhaustedError(error.retryAt);
    }
    if (error instanceof GatewayResponseError) {
      throw new ConnectError(error.message, Code.DataLoss);
    }
    throw upstreamFailure(error, signal);
  } finally {
    localAbort.abort(new Error("gateway native dispatch stopped"));
  }
}

function piCallFrame(payload) {
  let frame;
  try {
    frame = decodePayload(payload);
  } catch {
    throw new ConnectError("Pi payload is not valid JSON", Code.InvalidArgument);
  }
  if (!plainObject(frame) || !("context" in frame)) {
    throw new ConnectError("Pi payload has no call frame", Code.InvalidArgument);
  }
  if (frame.options !== undefined && !plainObject(frame.options)) {
    throw new ConnectError("Pi call options are not an object", Code.InvalidArgument);
  }
  return { context: frame.context, options: frame.options ?? {} };
}

// Credentials, arbitrary headers/environment, callbacks, client objects, and
// native transport/retry/timeout controls belong to the gateway process. They
// cannot be selected through the data plane. Every other JSON option is
// forwarded without a Cyclo allowlist.
function gatewayOptions(options, signal, api, onResponse, transformHeaders) {
  const {
    apiKey: _apiKey,
    signal: _signal,
    headers: _headers,
    env: _env,
    fetch: _fetch,
    telemetryContext: _telemetryContext,
    client: _client,
    onPayload: _onPayload,
    onResponse: _onResponse,
    transformHeaders: _transformHeaders,
    transport: _transport,
    timeoutMs: _timeoutMs,
    websocketConnectTimeoutMs: _websocketConnectTimeoutMs,
    maxRetries: _maxRetries,
    maxRetryDelayMs: _maxRetryDelayMs,
    ...inference
  } = options;
  return {
    ...inference,
    signal,
    maxRetries: 0,
    onResponse,
    transformHeaders,
    ...(api === "openai-codex-responses"
      ? { transport: "sse" }
      : {}),
  };
}

// Account exhaustion is never slept on here: before any output it becomes the
// compositional Provider RESOURCE_EXHAUSTED contract, so another component may
// select another route. All ambiguous failures remain ordinary failures.
function providerExhaustionRetryAt(api, response, event, now) {
  if (event?.type !== "error" || nativeErrorStatus(api, response, event) !== 429) {
    return undefined;
  }

  const nowMs = now();
  const waitMs = retryAfterMs(response?.headers, nowMs)
    ?? (api === "openai-codex-responses" ? codexResetWaitMs(event) : undefined)
    ?? DEFAULT_EXHAUSTION_RETRY_MS;
  const retryAt = new Date(nowMs + Math.max(1_000, waitMs));
  return Number.isFinite(retryAt.getTime())
    ? retryAt
    : new Date(nowMs + DEFAULT_EXHAUSTION_RETRY_MS);
}

function nativeErrorStatus(api, response, event) {
  if (Number.isInteger(response?.status)) return response.status;
  const message = event?.error?.errorMessage;
  if (typeof message !== "string") return undefined;

  // The pinned Anthropic and OpenAI Pi adapters catch SDK errors before their
  // onResponse hook runs. These exact prefixes are produced from the SDK's
  // numeric status by those pinned adapters; arbitrary body text follows them.
  if (api === "anthropic-messages" && /^429(?:\s|$)/u.test(message)) return 429;
  if (api === "openai-responses" && message.startsWith("OpenAI API error (429):")) {
    return 429;
  }
  return undefined;
}

function codexResetWaitMs(event) {
  const message = event?.error?.errorMessage;
  if (typeof message !== "string") return undefined;
  const match = /\bTry again in ~(\d+) min\./u.exec(message);
  if (!match) return undefined;
  const minutes = Number(match[1]);
  return Number.isSafeInteger(minutes) ? Math.max(1, minutes) * 60_000 : undefined;
}

function retryAfterMs(headers = {}, nowMs = Date.now()) {
  const millisecondsValue = headerValue(headers, "retry-after-ms");
  const milliseconds = Number(millisecondsValue);
  if (millisecondsValue?.trim() && Number.isFinite(milliseconds)) {
    return Math.max(0, milliseconds);
  }

  const value = headerValue(headers, "retry-after")?.trim();
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1_000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - nowMs) : undefined;
}

function headerValue(headers, name) {
  const value = typeof headers?.get === "function"
    ? headers.get(name)
    : headers?.[name];
  return typeof value === "string" ? value : undefined;
}

function eventUsage(event) {
  const usage = event?.type === "done"
    ? event.message?.usage
    : event?.type === "error"
      ? event.error?.usage
      : undefined;
  if (!plainObject(usage)) return undefined;
  return {
    inputTokens: safeTokens(usage.input) + safeTokens(usage.cacheRead) + safeTokens(usage.cacheWrite),
    outputTokens: safeTokens(usage.output),
    cachedInputTokens: safeTokens(usage.cacheRead),
    reasoningTokens: safeTokens(usage.reasoning),
  };
}

function safeTokens(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function credentialReflectionGuard(values) {
  const secrets = [];
  const partials = [];

  function add(candidates) {
    for (const candidate of candidates) {
      if (
        typeof candidate !== "string"
        || !candidate
        || secrets.includes(candidate)
      ) continue;
      secrets.push(candidate);
      partials.push(new Set());
    }
  }

  add(values);

  return Object.freeze({
    add,
    check(payload) {
      const fragments = stringFragments(JSON.parse(payload));
      for (const fragment of fragments) {
        for (let index = 0; index < secrets.length; index += 1) {
          if (advancesToCredential(secrets[index], partials[index], fragment)) {
            throw new GatewayResponseError(
              "upstream response contained gateway authentication material",
            );
          }
        }
      }
    },
  });
}

function stringFragments(document) {
  const fragments = [];
  const pending = [document];
  while (pending.length > 0) {
    const value = pending.pop();
    if (typeof value === "string") {
      fragments.push(value);
      continue;
    }
    if (value === null || typeof value !== "object") continue;
    const entries = Object.entries(value);
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const [key, child] = entries[index];
      pending.push(child);
      pending.push(key);
    }
  }
  return fragments;
}

function advancesToCredential(secret, partials, fragment) {
  if (fragment.includes(secret)) return true;

  const existing = [...partials];
  for (const matched of existing) {
    const remainder = secret.slice(matched);
    if (fragment.startsWith(remainder)) return true;
    if (remainder.startsWith(fragment)) {
      partials.add(matched + fragment.length);
    }
  }

  const limit = Math.min(secret.length - 1, fragment.length);
  for (let length = 1; length <= limit; length += 1) {
    if (fragment.endsWith(secret.slice(0, length))) partials.add(length);
  }
  return false;
}

function upstreamFailure(_error, signal) {
  if (signal?.aborted) {
    if (signal.reason instanceof ConnectError) return signal.reason;
    return new ConnectError("request canceled", Code.Canceled);
  }
  return new ConnectError("upstream inference failed", Code.Unavailable);
}

function plainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

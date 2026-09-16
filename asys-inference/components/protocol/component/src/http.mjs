import { createServer } from "node:http";

import { Code, ConnectError } from "@connectrpc/connect";

export const COMPONENT_API_HOST = "0.0.0.0";
export const COMPONENT_API_PORT = 8080;
export const MAX_JSON_REQUEST_BYTES = 1024 * 1024;

const closePromises = new WeakMap();
const CONNECT_ERRORS = new Map([
  [Code.Canceled, Object.freeze({ status: 499, code: "canceled" })],
  [Code.InvalidArgument, Object.freeze({ status: 400, code: "invalid_argument" })],
  [Code.DeadlineExceeded, Object.freeze({ status: 504, code: "deadline_exceeded" })],
  [Code.NotFound, Object.freeze({ status: 404, code: "not_found" })],
  [Code.AlreadyExists, Object.freeze({ status: 409, code: "already_exists" })],
  [Code.PermissionDenied, Object.freeze({ status: 403, code: "permission_denied" })],
  [Code.ResourceExhausted, Object.freeze({ status: 429, code: "resource_exhausted" })],
  [Code.FailedPrecondition, Object.freeze({ status: 412, code: "failed_precondition" })],
  [Code.Aborted, Object.freeze({ status: 409, code: "aborted" })],
  [Code.OutOfRange, Object.freeze({ status: 400, code: "out_of_range" })],
  [Code.Unimplemented, Object.freeze({ status: 501, code: "unimplemented" })],
  [Code.Unavailable, Object.freeze({ status: 503, code: "unavailable" })],
  [Code.Unauthenticated, Object.freeze({ status: 401, code: "unauthenticated" })],
]);

export class HostAPIError extends Error {
  constructor(message, { status = 400, code = "invalid_request", cause } = {}) {
    super(message, { cause });
    this.name = "HostAPIError";
    this.status = status;
    this.code = code;
  }
}

export function createHostAPIServer({ handler } = {}) {
  if (typeof handler !== "function") {
    throw new TypeError("host API handler must be a function");
  }
  const server = createServer((request, response) => {
    void Promise.resolve(handler(request, response)).catch((error) => {
      if (response.destroyed) return;
      if (response.headersSent) {
        response.destroy(error instanceof Error ? error : undefined);
        return;
      }
      sendAPIError(response, error);
    });
  });
  return server;
}

export async function listenHostAPIServer(
  server,
  { host = COMPONENT_API_HOST, port = COMPONENT_API_PORT } = {},
) {
  if (typeof host !== "string" || !host) {
    throw new TypeError("host API address must be a non-empty string");
  }
  if (!Number.isSafeInteger(port) || port < 0 || port > 65_535) {
    throw new TypeError("host API port must be an integer between 0 and 65535");
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

export function closeHostAPIServer(server) {
  const existing = closePromises.get(server);
  if (existing) return existing;
  if (!server.listening) return Promise.resolve();
  const closing = new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
    server.closeAllConnections?.();
  }).finally(() => closePromises.delete(server));
  closePromises.set(server, closing);
  return closing;
}

export function serverFailure(server) {
  return new Promise((_, reject) => server.once("error", reject));
}

export function requestURL(request) {
  try {
    return new URL(request.url ?? "", "http://component.invalid");
  } catch (error) {
    throw new HostAPIError("request URL is invalid", { cause: error });
  }
}

export function requestSignal(request, response, shutdownSignal) {
  const controller = new AbortController();
  let cleaned = false;
  const abort = (reason) => {
    if (!controller.signal.aborted) controller.abort(reason);
  };
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    request.off("aborted", onClose);
    response.off("close", onClose);
    response.off("finish", onFinish);
    shutdownSignal?.removeEventListener("abort", onShutdown);
  };
  const onClose = () => {
    if (!response.writableEnded) abort(new Error("host API client disconnected"));
    cleanup();
  };
  const onShutdown = () => {
    abort(shutdownSignal.reason);
    cleanup();
  };
  const onFinish = () => cleanup();
  request.once("aborted", onClose);
  response.once("close", onClose);
  response.once("finish", onFinish);
  shutdownSignal?.addEventListener("abort", onShutdown, { once: true });
  if (shutdownSignal?.aborted) onShutdown();
  return controller.signal;
}

export async function readJSON(
  request,
  { signal, maxBytes = MAX_JSON_REQUEST_BYTES } = {},
) {
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0) {
    throw new TypeError("maximum JSON request size must be a positive integer");
  }
  const rawContentType = request.headers["content-type"];
  const contentType = typeof rawContentType === "string"
    ? rawContentType.split(";", 1)[0].trim().toLowerCase()
    : undefined;
  if (contentType !== "application/json") {
    throw new HostAPIError("content-type must be application/json", {
      status: 415,
      code: "unsupported_media_type",
    });
  }
  const encoding = request.headers["content-encoding"];
  if (encoding !== undefined
      && (typeof encoding !== "string" || encoding.toLowerCase() !== "identity")) {
    throw new HostAPIError("content-encoding is not supported", {
      status: 415,
      code: "unsupported_content_encoding",
    });
  }
  const declared = request.headers["content-length"];
  if (declared !== undefined) {
    if (typeof declared !== "string" || !/^[0-9]+$/u.test(declared)) {
      throw new HostAPIError("content-length is invalid");
    }
    const length = Number(declared);
    if (!Number.isSafeInteger(length) || length < 0) {
      throw new HostAPIError("content-length is invalid");
    }
    if (length > maxBytes) {
      throw new HostAPIError("request body is too large", {
        status: 413,
        code: "request_too_large",
      });
    }
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    signal?.throwIfAborted();
    size += chunk.length;
    if (size > maxBytes) {
      throw new HostAPIError("request body is too large", {
        status: 413,
        code: "request_too_large",
      });
    }
    chunks.push(chunk);
  }
  let document;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(
      Buffer.concat(chunks),
    );
    document = JSON.parse(text);
  } catch (error) {
    throw new HostAPIError("request body must be valid JSON", {
      code: "invalid_json",
      cause: error,
    });
  }
  if (document === null || typeof document !== "object" || Array.isArray(document)) {
    throw new HostAPIError("request body must be a JSON object");
  }
  return document;
}

export function sendJSON(response, status, document) {
  const body = Buffer.from(`${JSON.stringify(document)}\n`, "utf8");
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": body.length,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  response.end(body);
}

export function methodNotAllowed(response, allowed) {
  response.setHeader("allow", allowed.join(", "));
  throw new HostAPIError("method not allowed", {
    status: 405,
    code: "method_not_allowed",
  });
}

export function notFound(pathname) {
  throw new HostAPIError(`unknown endpoint: ${safeText(pathname)}`, {
    status: 404,
    code: "not_found",
  });
}

function sendAPIError(response, error) {
  const normalized = apiError(error);
  sendJSON(response, normalized.status, {
    error: {
      code: normalized.code,
      message: safeText(normalized.message),
    },
  });
}

function apiError(error) {
  if (error instanceof HostAPIError) return error;
  if (error instanceof ConnectError) {
    const mapped = CONNECT_ERRORS.get(error.code)
      ?? Object.freeze({ status: 500, code: "internal" });
    return new HostAPIError(error.rawMessage || "component request failed", {
      status: mapped.status,
      code: mapped.code,
      cause: error,
    });
  }
  return new HostAPIError("component request failed", {
    status: 500,
    code: "internal",
    cause: error,
  });
}

function safeText(value) {
  return String(value).replace(/[\u0000-\u001f\u007f]/gu, " ").slice(0, 512);
}

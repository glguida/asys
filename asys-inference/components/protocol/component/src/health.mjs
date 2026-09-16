import { COMPONENT_API_PORT } from "./http.mjs";

export async function checkComponentHealth({
  url = `http://127.0.0.1:${COMPONENT_API_PORT}/health`,
  timeoutMs = 1_000,
} = {}) {
  if (typeof url !== "string" || !url) {
    throw new TypeError("health URL must be a non-empty string");
  }
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError("health timeout must be a positive integer");
  }
  const response = await fetch(url, {
    headers: { accept: "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!response.ok) return false;
  const document = await response.json();
  return document?.status === "ready";
}

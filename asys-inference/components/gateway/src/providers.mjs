import { isProviderPrefix } from "@cyclo/provider/protocol";

import { getPiProviders, validatePiProviders } from "./pi-registry.mjs";

const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/u;

export function discoverSupportedProviders({
  providers = getPiProviders(),
} = {}) {
  validatePiProviders(providers);

  const supported = providers.map((provider) => {
    const models = provider.getModels();
    if (
      !Array.isArray(models)
      || models.some((model) => !model || typeof model !== "object")
    ) {
      throw new Error(`pi-ai returned invalid models for provider ${provider.id}`);
    }
    if (
      typeof provider.name !== "string"
      || !provider.name.trim()
      || CONTROL_CHARACTER.test(provider.name)
    ) {
      throw new Error(`pi-ai returned an invalid name for provider ${provider.id}`);
    }
    return provider;
  });
  return Object.freeze(supported.sort((left, right) => left.id.localeCompare(right.id)).map((provider) => {
    const supportsOAuth = typeof provider.auth.oauth?.login === "function";
    const supportsApiKey = typeof provider.auth.apiKey?.resolve === "function";
    if (!supportsOAuth && !supportsApiKey) {
      throw new Error(`built-in provider ${provider.id} has no supported authentication`);
    }
    return Object.freeze({
      id: provider.id,
      description: provider.name,
      oauth: supportsOAuth,
      apiKey: supportsApiKey,
    });
  }));
}

export function formatSupportedProviders(providers = discoverSupportedProviders()) {
  if (!Array.isArray(providers)) throw new TypeError("providers must be an array");
  const rows = [];
  for (const provider of providers) {
    if (
      !provider
      || typeof provider !== "object"
      || !isProviderPrefix(provider.id)
      || typeof provider.description !== "string"
      || !provider.description
      || CONTROL_CHARACTER.test(provider.description)
      || typeof provider.oauth !== "boolean"
      || typeof provider.apiKey !== "boolean"
      || (!provider.oauth && !provider.apiKey)
    ) {
      throw new Error("cannot format an invalid provider description");
    }
    const auth = provider.oauth && provider.apiKey
      ? "oauth or api-key"
      : provider.oauth
        ? "oauth"
        : "api-key";
    const login = `cyclo gateway login ${provider.id}`;
    rows.push([provider.id, provider.description, auth, login]);
  }
  const widths = rows.reduce(
    (current, row) => current.map((width, index) => Math.max(width, row[index].length)),
    ["PROVIDER".length, "DESCRIPTION".length, "AUTH".length, "LOGIN COMMAND".length],
  );
  const format = (row) => row.map((value, index) => value.padEnd(widths[index])).join("  ").trimEnd();
  const lines = [
    "Supported gateway providers (login creates an account in the credential store):",
    format(["PROVIDER", "DESCRIPTION", "AUTH", "LOGIN COMMAND"]),
    format(widths.map((width) => "-".repeat(width))),
    ...rows.map(format),
    "",
    "Use --as NAME to give an account a distinct catalogue prefix.",
    "Login selects OAuth when available, otherwise Pi's native API-key or ambient setup.",
    "Use --api-key-stdin or --api-key-env for a specific API-key login; piped input supplies one secret.",
    "OAuth URLs and device codes use the attached terminal; no login is needed to list this table.",
  ];
  return lines.join("\n");
}

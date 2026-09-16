import { enumToJson } from "@bufbuild/protobuf";
import { HealthStatus } from "@cyclo/component/contract";
import {
  createHostAPIServer,
  methodNotAllowed,
  notFound,
  requestSignal,
  requestURL,
  sendJSON,
} from "@cyclo/component/http";

import { ModalitySchema } from "../gen/cyclo/provider/v1/provider_pb.js";

export function createProviderHTTPServer({
  component,
  provider,
  shutdownSignal,
} = {}) {
  if (!component || typeof component.health !== "function") {
    throw new TypeError("a component health implementation is required");
  }
  if (!provider || typeof provider.listModels !== "function") {
    throw new TypeError("a Provider implementation is required");
  }
  return createHostAPIServer({
    shutdownSignal,
    async handler(request, response) {
      const url = requestURL(request);
      const context = {
        signal: requestSignal(request, response, shutdownSignal),
      };
      if (url.pathname === "/health") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        const health = await component.health({}, context);
        const ready = health?.status === HealthStatus.READY;
        sendJSON(response, ready ? 200 : 503, {
          status: ready ? "ready" : "not_ready",
          message: safeMessage(health?.message),
        });
        return;
      }
      if (url.pathname === "/v1/models") {
        if (request.method !== "GET") return methodNotAllowed(response, ["GET"]);
        const catalogue = await provider.listModels({}, context);
        sendJSON(response, 200, providerCatalogueDocument(catalogue));
        return;
      }
      return notFound(url.pathname);
    },
  });
}

/**
 * Convert the private Provider message into the component-owned HTTP schema.
 *
 * This deliberately does not use protobuf Any JSON encoding: an outer
 * component must be able to carry an extension whose descriptor is unknown to
 * Cyclo. The REST representation therefore keeps each Any as an explicit
 * typeUrl/base64 pair.
 */
export function providerCatalogueDocument(catalogue) {
  return {
    models: (catalogue?.models ?? []).map(modelDocument),
  };
}

function modelDocument(model) {
  const capabilities = model?.capabilities;
  const document = {
    id: model?.id ?? "",
    displayName: model?.displayName ?? "",
    capabilities: {
      inputModalities: (capabilities?.inputModalities ?? []).map(modalityName),
      outputModalities: (capabilities?.outputModalities ?? []).map(modalityName),
      functionTools: capabilities?.functionTools ?? false,
      parallelToolCalls: capabilities?.parallelToolCalls ?? false,
      reasoningSummaries: capabilities?.reasoningSummaries ?? false,
      temperature: capabilities?.temperature ?? false,
      topP: capabilities?.topP ?? false,
      stopSequences: capabilities?.stopSequences ?? false,
      extensionTypes: capabilities?.extensionTypes ?? [],
      reasoning: capabilities?.reasoning ?? false,
    },
    extensions: (model?.extensions ?? []).map(extensionDocument),
    inferenceFormat: model?.inferenceFormat ?? "",
  };
  if (model?.contextWindowTokens !== undefined) {
    document.contextWindowTokens = decimal(model.contextWindowTokens);
  }
  if (model?.maxOutputTokens !== undefined) {
    document.maxOutputTokens = decimal(model.maxOutputTokens);
  }
  return document;
}

function modalityName(value) {
  const name = enumToJson(ModalitySchema, value);
  if (typeof name !== "string") {
    throw new TypeError("Provider catalogue contains an unknown modality");
  }
  return name;
}

function extensionDocument(value) {
  if (typeof value?.typeUrl !== "string" || !(value?.value instanceof Uint8Array)) {
    throw new TypeError("Provider catalogue contains an invalid extension");
  }
  return {
    typeUrl: value.typeUrl,
    value: Buffer.from(value.value).toString("base64"),
  };
}

function decimal(value) {
  if (typeof value === "bigint" && value >= 0n) return value.toString();
  if (Number.isSafeInteger(value) && value >= 0) return String(value);
  throw new TypeError("Provider catalogue contains an invalid token limit");
}

function safeMessage(value) {
  return typeof value === "string"
    ? value.replace(/[\u0000-\u001f\u007f]/gu, " ").slice(0, 512)
    : "";
}

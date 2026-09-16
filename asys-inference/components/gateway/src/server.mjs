import { readFile } from "node:fs/promises";
import { resolveBindings } from "@cyclo/component/bindings";
import { parseDeclaration } from "@cyclo/component/declaration";
import { createComponentServer } from "@cyclo/component/server";
import { Provider } from "@cyclo/provider/contract";

const defaultComponentConf = new URL("../component.conf", import.meta.url);

export async function createGatewayServer({
  services,
  shutdownSignal,
  componentConf = defaultComponentConf,
} = {}) {
  const declaration = parseDeclaration(await readFile(componentConf, "utf8"), {
    source: componentConf instanceof URL ? componentConf.pathname : String(componentConf),
  });
  const bindings = resolveBindings(declaration, [Provider]);

  if (
    bindings.requires.size !== 0
    || bindings.provides.size !== 1
    || bindings.provides.get(Provider.typeName) !== Provider
  ) {
    throw new TypeError("the gateway must provide exactly one Provider output");
  }

  const implementations = new Map([
    [Provider.typeName, services?.provider],
  ]);

  return createComponentServer({ bindings, implementations, shutdownSignal });
}

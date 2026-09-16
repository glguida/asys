const CONTROL = /[\u0000-\u001f\u007f]/u;

export function createAuthInteraction({
  ask,
  askSecret = ask,
  write = console.log,
  signal,
  allowOption = () => true,
}) {
  if (
    typeof ask !== "function"
    || typeof askSecret !== "function"
    || typeof write !== "function"
    || typeof allowOption !== "function"
  ) {
    throw new TypeError(
      "authentication interaction requires ask, askSecret, write, and allowOption functions",
    );
  }
  // pi-ai's public AuthInteraction accepts an optional operation signal. Provider
  // flows can also cancel an individual prompt when an out-of-band callback
  // wins, so preserve both cancellation scopes here.
  const operationSignal = signal ?? new AbortController().signal;
  return Object.freeze({
    signal: operationSignal,
    async prompt(prompt) {
      if (!prompt || typeof prompt !== "object") {
        throw new Error("authentication provider emitted an invalid prompt");
      }
      const signal = prompt.signal === undefined
        ? operationSignal
        : AbortSignal.any([operationSignal, prompt.signal]);
      const promptAsk = (question) => ask(question, signal);
      const promptSecret = (question) => askSecret(question, signal);
      if (prompt.type === "select") {
        const selected = await selectAuthOption(prompt, promptAsk, write, { allowOption });
        if (selected === undefined) throw new Error("Login cancelled");
        return selected;
      }
      if (!["text", "secret", "manual_code"].includes(prompt.type)) {
        throw new Error(`authentication provider emitted an unknown prompt type: ${prompt.type}`);
      }
      const message = display(prompt.message, "prompt message");
      const placeholder = prompt.placeholder
        ? ` (${display(prompt.placeholder, "prompt placeholder")})`
        : "";
      const question = `${message}${placeholder} `;
      return prompt.type === "secret" ? promptSecret(question) : promptAsk(question);
    },
    notify(event) {
      if (!event || typeof event !== "object") {
        throw new Error("authentication provider emitted an invalid event");
      }
      if (event.type === "info") {
        write(display(event.message, "information message"));
        for (const link of event.links ?? []) {
          const label = link.label ? `${display(link.label, "link label")}: ` : "";
          write(`${label}${display(link.url, "information URL")}`);
        }
        return;
      }
      if (event.type === "auth_url") {
        write(`\nOpen this URL to authorize the gateway:\n  ${display(event.url, "authorization URL")}`);
        if (event.instructions) write(display(event.instructions, "authorization instructions"));
        return;
      }
      if (event.type === "device_code") {
        write(`\nOpen this URL:\n  ${display(event.verificationUri, "device-code URL")}`);
        write(`Enter code: ${display(event.userCode, "device code")}`);
        return;
      }
      if (event.type === "progress") {
        write(display(event.message, "progress message"));
        return;
      }
      throw new Error(`authentication provider emitted an unknown event type: ${event.type}`);
    },
  });
}

export async function selectAuthOption(
  prompt,
  ask,
  write = console.log,
  { allowOption = () => true } = {},
) {
  if (!Array.isArray(prompt?.options) || prompt.options.length === 0) {
    throw new Error("OAuth selection prompt has no options");
  }
  const options = prompt.options.filter((option) => allowOption(option));
  if (options.length === 0) {
    throw new Error("authentication provider has no gateway-compatible options");
  }
  if (options.length !== prompt.options.length) {
    write(
      "Options requiring host credential files or environment are unavailable "
      + "inside the gateway.",
    );
  }
  write(`\n${display(prompt.message, "selection message")}`);
  const ids = new Set();
  for (const [index, option] of options.entries()) {
    const id = display(option?.id, "selection id");
    if (ids.has(id)) throw new Error(`OAuth selection prompt repeats option ${id}`);
    ids.add(id);
    const description = option?.description
      ? ` — ${display(option.description, "selection description")}`
      : "";
    write(`  ${index + 1}. ${display(option?.label, "selection label")}${description}`);
  }
  while (true) {
    const answer = String(await ask(`Enter number (1-${options.length}) [1]: `)).trim();
    if (!answer) return options[0].id;
    if (["q", "quit", "cancel"].includes(answer.toLowerCase())) return undefined;
    if (/^[0-9]+$/u.test(answer)) {
      const selected = options[Number(answer) - 1];
      if (selected) return selected.id;
    }
    write(`Choose 1-${options.length}, or q to cancel.`);
  }
}

function display(value, label) {
  if (typeof value !== "string" || !value.trim() || CONTROL.test(value)) {
    throw new Error(`authentication ${label} must be non-empty display text`);
  }
  return value;
}

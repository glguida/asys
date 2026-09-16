import { readFileSync } from 'node:fs';
import { Actions, actionTools } from '../src/actions.mjs';

// Explicitly enabled by a coordinator's environment command. The generic
// agent runner does not inspect workflow metadata or install action tools.
export default function bpmnExtension(pi) {
  const request = JSON.parse(readFileSync(process.env.ASYS_REQUEST, 'utf8'));
  const actions = request.metadata.actions;
  if (!actions) return;
  if (actions.version !== 1 || !Array.isArray(actions.entries)) throw new Error('Coordinator requires an actions assignment');
  for (const tool of actionTools(new Actions(process.env.ASYS_JOB_DIR, actions.entries))) pi.registerTool(tool);
}

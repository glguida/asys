import { humanClient } from './client.mjs';
import { readJSON } from './files.mjs';
import { requiredString } from './values.mjs';

export async function human({ job, argv, env, signal }, { id = job.id, metadata = {} } = {}) {
  if (argv.length) throw new Error('asys-human takes its task description from the job input');
  const component = requiredString(env.DCOMP_COMPONENT_NAME, 'DCOMP_COMPONENT_NAME');
  const request = readJSON(env.ASYS_REQUEST);
  const input = job.input?.prompt === undefined && typeof job.input?.request === 'string'
    ? { ...job.input, prompt: job.input.request } : job.input;
  const { resultJson } = await humanClient('human', env).ask({
    id: `${component}.${id}`,
    inputJson: JSON.stringify(input),
    metadataJson: JSON.stringify({ ...request.metadata, ...metadata, component, job_id: job.id, files: { workspace: job.workspace } }),
  }, { signal });
  return JSON.parse(resultJson);
}

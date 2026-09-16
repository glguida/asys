import { resolve } from 'node:path';
import { readJSON, writeJSON } from './files.mjs';
import { requiredString } from './values.mjs';

export async function program(execute, { env = process.env, argv = process.argv.slice(2) } = {}) {
  const job = {
    id: requiredString(env.ASYS_JOB_ID, 'ASYS_JOB_ID'),
    directory: resolve(requiredString(env.ASYS_JOB_DIR, 'ASYS_JOB_DIR')),
    workspace: resolve(requiredString(env.ASYS_WORKSPACE, 'ASYS_WORKSPACE')),
    input: readJSON(requiredString(env.ASYS_INPUT, 'ASYS_INPUT')),
    result: resolve(requiredString(env.ASYS_RESULT, 'ASYS_RESULT')),
  };
  const controller = new AbortController();
  const stop = () => controller.abort(new Error('Job stopping'));
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
  try {
    const result = await execute({ job, argv, env, signal: controller.signal });
    controller.signal.throwIfAborted();
    writeJSON(job.result, result);
    if (result && typeof result === 'object' && Object.hasOwn(result, 'exception') && result.exception !== null) {
      throw new Error(requiredString(result.exception, 'Job exception'));
    }
  } finally {
    process.removeListener('SIGTERM', stop);
    process.removeListener('SIGINT', stop);
  }
}

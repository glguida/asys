import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { Queue } from '../javascript/queue.mjs';

export class PreparedQueue extends Queue {
  async resubmit(previous, id) {
    const request = await this.request(previous);
    return this.submit(request.type, id, { input: request.input, args: request.args,
      metadata: { ...request.metadata, retry_of: previous }, workspace: (await this.paths(previous)).workspace });
  }
  executionDirectory(id) { return join(this.root, 'executions', id, 'job'); }
  workspace(id) { return join(this.root, 'executions', id, 'workspace'); }
  async submit(type, id, options = {}) {
    const directory = this.executionDirectory(id), workspace = this.workspace(id);
    await mkdir(directory, { recursive: true });
    await mkdir(workspace, { recursive: true });
    return super.submit(type, id, { directory, workspace, ...options });
  }
}

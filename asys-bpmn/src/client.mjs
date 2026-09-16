import { createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-node';
import { inputHttpOptions } from '@dcomp/component';
import { Workflow } from '../gen/asys/workflow/v1/workflow_pb.js';

export function workflowClient(input = 'workflow', env = process.env) {
  return createClient(Workflow, createConnectTransport({
    baseUrl: 'http://dcomp', httpVersion: '1.1', nodeOptions: inputHttpOptions(input, env),
  }));
}

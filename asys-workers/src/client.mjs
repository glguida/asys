import { createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-node';
import { inputHttpOptions } from '@dcomp/component';
import { Human } from '../gen/asys/human/v1/human_pb.js';

export function humanClient(input = 'human', env = process.env) {
  return createClient(Human, createConnectTransport({
    baseUrl: 'http://dcomp', httpVersion: '1.1', nodeOptions: inputHttpOptions(input, env),
  }));
}

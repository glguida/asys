import { createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-node';
import { inputHttpOptions } from '@dcomp/component';
import { Human } from '@asys/human-protocol';

export function humanClient(input, env = process.env) {
  return createClient(Human, createConnectTransport({
    baseUrl: 'http://dcomp', httpVersion: '1.1', nodeOptions: inputHttpOptions(input, env),
    readMaxBytes: 8 * 1024 * 1024, writeMaxBytes: 8 * 1024 * 1024,
  }));
}

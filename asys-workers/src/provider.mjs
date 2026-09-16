import { createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-node';
import { inputHttpOptions } from '@dcomp/component';
import { Provider } from '@cyclo/provider/contract';

export function providerClient(env = process.env) {
  return createClient(Provider, createConnectTransport({
    baseUrl: 'http://dcomp', httpVersion: '1.1', nodeOptions: inputHttpOptions('inference', env),
  }));
}

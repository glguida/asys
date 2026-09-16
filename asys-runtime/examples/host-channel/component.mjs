// The component side: runs inside a container with the shared root mounted at
// /var/lib/asys-runtime. Announces itself on `out`, waits for the host's reply
// on `in`, then reports completion. Only channel events cross the boundary.
import { Reader, Writer, directionRoot } from '/opt/asys/asys-runtime/javascript/channel.mjs';

const root = process.env.ASYS_RUNTIME_ROOT ?? '/var/lib/asys-runtime';
const name = process.argv[2] ?? 'demo';
const out = await new Writer(directionRoot(root, name, 'out')).ready();
const inbound = await new Reader(directionRoot(root, name, 'in')).ready();

const hello = await out.send('hello', { component: name, question: 'what is 6 * 7?', pid: process.pid });
console.log(`component: sent hello as event ${hello.sequence}`);

for await (const event of inbound.follow(undefined, { timeoutMs: 10_000 })) {
  console.log(`component: received ${event.type} #${event.sequence} ${JSON.stringify(event.data)}`);
  await inbound.advance(event.sequence);
  if (event.type === 'reply' && event.data?.to === hello.sequence) {
    const done = await out.send('done', { answer: event.data.answer, acknowledged: event.sequence });
    console.log(`component: sent done as event ${done.sequence}`);
    process.exit(0);
  }
}
console.error('component: no reply within 10 s');
process.exit(1);

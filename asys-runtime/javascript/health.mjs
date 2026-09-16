import { writeFileSync, rmSync } from 'node:fs';

export function heartbeat(path) {
  const tick = () => writeFileSync(path, '', { mode: 0o600 });
  tick();
  const timer = setInterval(tick, 2000);
  timer.unref();
  return () => { clearInterval(timer); rmSync(path, { force: true }); };
}

import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';

class CDP {
  constructor(child) {
    this.child = child; this.pending = new Map(); this.next = 0; this.buffer = '';
    child.on('exit', (code, signal) => {
      for (const {reject, timer} of this.pending.values()) { clearTimeout(timer); reject(Error(`Chrome exited: ${code ?? signal}`)); }
      this.pending.clear();
    });
    child.stdio[4].on('data', chunk => {
      this.buffer += chunk;
      let end;
      while ((end = this.buffer.indexOf('\0')) >= 0) {
        const message = JSON.parse(this.buffer.slice(0, end)); this.buffer = this.buffer.slice(end + 1);
        if (!this.pending.has(message.id)) continue;
        const {resolve, reject, timer} = this.pending.get(message.id); this.pending.delete(message.id); clearTimeout(timer);
        message.error ? reject(Error(message.error.message)) : resolve(message.result);
      }
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(Error(`Timed out: ${method}`)); }, 10000);
      this.pending.set(id, {resolve, reject, timer});
      this.child.stdio[3].write(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}) + '\0');
    });
  }
}

const chrome = process.env.CHROME_BIN ?? '/opt/google/chrome/chrome';
test('real browser: dragging console selections scrolls only that box, grows selection and stops cleanly', {
  skip: process.env.ASYS_DASHBOARD_BROWSER !== '1' || !existsSync(chrome), timeout: 25000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-console-selection-'));
  const source = new URL('../python/asys/dashboard_assets/console-selection.mjs', import.meta.url);
  const server = createServer(async (req, res) => {
    if (req.url === '/console-selection.mjs') {
      res.setHeader('Content-Type', 'text/javascript'); res.end(await readFile(source)); return;
    }
    res.setHeader('Content-Type', 'text/html');
    res.end(`<style>body{margin:0;font:16px/24px monospace}.spacer{height:900px}.console-content{height:240px;width:620px;margin:0 60px;overflow:auto;scrollbar-gutter:stable;overscroll-behavior:contain;white-space:pre-wrap}.line{display:block}button{font:inherit}</style>
      <div class="spacer">Page before the console</div><div id="box" class="console-content" tabindex="0"><button id="control">Console control</button></div><div class="spacer">Page after the console</div>
      <script type="module">import{installConsoleSelection}from'/console-selection.mjs';window.starts=0;window.clicks=0;window.follow=true;
      for(let i=0;i<200;i++){const line=document.createElement('span');line.className='line';line.textContent='Recorded line '+String(i).padStart(3,'0')+' with useful selectable evidence.';box.append(line)}
      control.onclick=()=>clicks++;window.teardown=installConsoleSelection(box,{onSelectionStart(){starts++;follow=false}});window.ready=true;</script>`);
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const child = spawn(chrome, ['--headless=new', '--ozone-platform=headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
    '--no-first-run', '--no-default-browser-check', '--remote-debugging-pipe', `--user-data-dir=${root}/chrome`, 'about:blank'], {
    stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'], env: {...process.env, DISPLAY: '', WAYLAND_DISPLAY: '',
      XAUTHORITY: join(root, 'none'), XDG_CONFIG_HOME: join(root, 'config'), XDG_CACHE_HOME: join(root, 'cache')},
  });
  child.stderr.resume();
  t.after(async () => {
    child.kill('SIGKILL'); child.stdio[3].destroy(); child.stdio[4].destroy(); server.closeAllConnections();
    await new Promise(resolve => server.close(resolve)); await rm(root, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  });
  const cdp = new CDP(child), {targetId} = await cdp.send('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await cdp.send('Target.attachToTarget', {targetId, flatten: true});
  const evaluate = async expression => {
    const response = await cdp.send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true}, sessionId);
    if (response.exceptionDetails) throw Error(JSON.stringify(response.exceptionDetails)); return response.result.value;
  };
  const mouse = (type, x, y, extra = {}) => cdp.send('Input.dispatchMouseEvent', {
    type, x, y, ...(type === 'mousePressed' || type === 'mouseReleased' ? {button: 'left', clickCount: 1} : {}), ...extra,
  }, sessionId);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  await cdp.send('Emulation.setDeviceMetricsOverride', {width: 1000, height: 700, deviceScaleFactor: 1, mobile: false}, sessionId);
  await cdp.send('Page.navigate', {url: `http://127.0.0.1:${server.address().port}/`}, sessionId);
  for (let i = 0; !await evaluate('window.ready === true'); i++) { assert.ok(i < 100); await pause(20); }
  await evaluate('window.scrollTo(0,700)');
  const metrics = () => evaluate('({page:scrollY,inner:box.scrollTop,selected:getSelection().toString().length,starts,follow,anchorInside:box.contains(getSelection().anchorNode),focusInside:box.contains(getSelection().focusNode)})');

  for (const direction of ['down', 'up']) {
    await evaluate(`box.scrollTop=${direction === 'down' ? 250 : 2000};getSelection().removeAllRanges();follow=true`);
    const rect = await evaluate('(()=>{const r=box.getBoundingClientRect();return{top:r.top,bottom:r.bottom}})()');
    const before = await metrics(), startY = rect.top + 120, endY = direction === 'down' ? 697 : 2;
    await mouse('mousePressed', 190, startY, {buttons: 1});
    await mouse('mouseMoved', 270, startY + (direction === 'down' ? 40 : -40), {buttons: 1});
    await mouse('mouseMoved', 280, endY, {buttons: 1});
    const first = await metrics();
    await pause(450);
    const held = await metrics();
    assert.equal(held.page, before.page, `${direction}: document must not move`);
    assert.ok(direction === 'down' ? held.inner > before.inner + 100 : held.inner < before.inner - 100, `${direction}: inner box scrolls`);
    assert.ok(held.selected > first.selected + 100, `${direction}: held drag extends selected text`);
    assert.equal(held.starts, before.starts + 1); assert.equal(held.follow, false);
    assert.equal(held.anchorInside && held.focusInside, true);
    await mouse('mouseReleased', 280, endY, {buttons: 0});
    const released = await metrics(); await pause(160);
    assert.deepEqual(await metrics(), released, 'mouseup stops scrolling and retains selection');
  }

  await evaluate('box.scrollTop=700');
  await mouse('mousePressed', 190, 320, {buttons: 1});
  await mouse('mouseMoved', 280, 697, {buttons: 1});
  await pause(100); await evaluate('window.dispatchEvent(new Event("blur"))');
  const blurred = await metrics(); await pause(160);
  assert.deepEqual(await metrics(), blurred, 'blur stops a drag');
  await mouse('mouseReleased', 280, 697, {buttons: 0});

  await evaluate('box.scrollTop=0;getSelection().removeAllRanges()');
  const button = await evaluate('(()=>{const r=control.getBoundingClientRect();return{x:r.x+20,y:r.y+10}})()');
  const starts = await evaluate('starts');
  await mouse('mousePressed', button.x, button.y, {buttons: 1});
  await mouse('mouseReleased', button.x, button.y, {buttons: 0});
  assert.equal(await evaluate('clicks'), 1); assert.equal(await evaluate('starts'), starts, 'control click does not start selection');
  const scrollbar = await evaluate('(()=>{const r=box.getBoundingClientRect(),width=box.offsetWidth-box.clientWidth;return{x:r.right-width/2,y:r.bottom-30,width}})()');
  assert.ok(scrollbar.width > 0, 'fixture has a native scrollbar');
  await mouse('mousePressed', scrollbar.x, scrollbar.y, {buttons: 1});
  await mouse('mouseReleased', scrollbar.x, scrollbar.y, {buttons: 0});
  await pause(180);
  assert.ok(await evaluate('box.scrollTop') > 0, 'native scrollbar still moves console');
  assert.equal(await evaluate('starts'), starts, 'scrollbar does not begin text selection');
  const oldPage = await evaluate('scrollY');
  await mouse('mouseWheel', 900, 500, {deltaX: 0, deltaY: 220}); await pause(180);
  assert.ok(await evaluate('scrollY') > oldPage, 'normal page wheel scrolling remains available');
  await evaluate('teardown()');
  await cdp.send('Browser.close');
});

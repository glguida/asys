import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {memberLayout} from '../asys-workers/worlds/torus/view.mjs';

const viewURL=new URL('../asys-workers/worlds/torus/view.mjs',import.meta.url);
const overlap=(a,b)=>a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y;
function assertLayout(positions) {
  const layout=memberLayout(positions);assert.equal(layout.length,Object.keys(positions).length);
  const boxes=layout.map(member=>{
    const stroke=Math.min(1.4,member.size*.16),half=(member.size+stroke)/2;
    const box={x:member.x-half,y:member.y-half,width:half*2,height:half*2};
    assert.ok(box.x>member.cell.x*40&&box.x+box.width<(member.cell.x+1)*40);
    assert.ok(box.y>member.cell.y*40+18&&box.y+box.height<(member.cell.y+1)*40);
    return box;
  });
  for(let i=0;i<boxes.length;i++)for(let j=0;j<i;j++)assert.equal(overlap(boxes[i],boxes[j]),false,`members ${i},${j} overlap`);
  return layout;
}
test('co-located members remain separate from one another and the artifact strip',()=>{
  for(const count of [1,2,3,4,8,16,32,64,256])assertLayout(Object.fromEntries(Array.from({length:count},(_,i)=>[`agent-${String(i).padStart(3,'0')}`,{x:2,y:3}])));
});
test('member layout is deterministic across map insertion order and preserves actual cells',()=>{
  const positions={z:{x:2,y:1},a:{x:0,y:0},b:{x:2,y:1},c:{x:2,y:1}};
  const original=JSON.stringify(positions),layout=assertLayout(positions);
  assert.deepEqual(layout,memberLayout(Object.fromEntries(Object.entries(positions).reverse())));
  assert.equal(JSON.stringify(positions),original);
});
test('route sample keeps the same torus renderer',async()=>{
  assert.equal(await readFile(viewURL,'utf8'),await readFile(new URL('../asys-workers/worlds/samples/route/env/worlds/torus/view.mjs',import.meta.url),'utf8'));
});

class CDP {
  constructor(child) {
    this.child=child; this.pending=new Map(); this.next=0; this.buffer='';
    child.on('exit',(code,signal)=>{
      for(const {reject,timer} of this.pending.values()){clearTimeout(timer);reject(Error(`Chrome exited: ${code ?? signal}`));}
      this.pending.clear();
    });
    child.stdio[4].on('data',chunk=>{
      this.buffer+=chunk;
      let end;
      while((end=this.buffer.indexOf('\0'))>=0) {
        const message=JSON.parse(this.buffer.slice(0,end)); this.buffer=this.buffer.slice(end+1);
        if(this.pending.has(message.id)) {
          const {resolve,reject,timer}=this.pending.get(message.id); this.pending.delete(message.id); clearTimeout(timer);
          message.error ? reject(Error(message.error.message)) : resolve(message.result);
        }
      }
    });
  }
  send(method,params={},sessionId) {
    const id=++this.next;
    return new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{this.pending.delete(id);reject(Error(`Timed out: ${method}`));},10000);
      this.pending.set(id,{resolve,reject,timer});
      this.child.stdio[3].write(JSON.stringify({id,method,params,...sessionId?{sessionId}:{}})+'\0');
    });
  }
}

const chrome=process.env.CHROME_BIN??'/opt/google/chrome/chrome';
test('browser: every shared-cell triangle supports pointer, keyboard and local inspection',{
  skip:process.env.ASYS_DASHBOARD_BROWSER!=='1'||!existsSync(chrome),timeout:20000,
},async t=>{
  const root=await mkdtemp(join(tmpdir(),'asys-torus-browser-'));
  const agents=['agent-001','agent-002','agent-003','agent-004'];
  const frame={state:{agents,positions:Object.fromEntries(agents.map(id=>[id,{x:1,y:1}])),settings:{width:3,height:3,radius:1,visible_artifacts:4,direction:'minimize'},baseline:null,
    artifacts:[{id:'a',agent:agents[0],score:8,position:{x:1,y:1}},{id:'b',agent:agents[1],score:9,position:{x:1,y:1}},{id:'far',agent:agents[1],score:12,position:{x:0,y:0}}]}};
  const server=createServer(async(req,res)=>{
    if(req.url==='/view.mjs'){res.setHeader('Content-Type','text/javascript');res.end(await readFile(viewURL));return;}
    if(req.url==='/styles.css'){res.setHeader('Content-Type','text/css');res.end(await readFile(new URL('../designs/default/styles.css',import.meta.url)));return;}
    res.setHeader('Content-Type','text/html');res.end(`<link rel="stylesheet" href="/styles.css"><div id="world"></div><script type="module">import{mount}from'/view.mjs';window.members=[];window.inspections=[];window.frame=${JSON.stringify(frame)};window.viewer=mount(document.querySelector('#world'),{selectMember:id=>members.push(id),inspect:value=>inspections.push(value)});viewer.update(frame);</script>`);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const child=spawn(chrome,['--headless=new','--ozone-platform=headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--no-default-browser-check','--remote-debugging-pipe',`--user-data-dir=${root}/chrome`,'about:blank'],{stdio:['ignore','ignore','pipe','pipe','pipe'],env:{...process.env,DISPLAY:'',WAYLAND_DISPLAY:'',XAUTHORITY:join(root,'none'),XDG_CONFIG_HOME:join(root,'config'),XDG_CACHE_HOME:join(root,'cache')}});
  child.stderr.resume();t.after(async()=>{child.kill('SIGKILL');child.stdio[3].destroy();child.stdio[4].destroy();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true,maxRetries:8,retryDelay:100});});
  const cdp=new CDP(child),{targetId}=await cdp.send('Target.createTarget',{url:'about:blank'}),{sessionId}=await cdp.send('Target.attachToTarget',{targetId,flatten:true});
  const evaluate=async expression=>{const value=await cdp.send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sessionId);if(value.exceptionDetails)throw Error(JSON.stringify(value.exceptionDetails));return value.result.value;};
  const wait=async expression=>{const end=Date.now()+5000;while(!await evaluate(expression)){if(Date.now()>end)throw Error(expression);await new Promise(resolve=>setTimeout(resolve,30));}};
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1100,height:900,deviceScaleFactor:1,mobile:false},sessionId);
  await cdp.send('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/`},sessionId);
  await wait('document.querySelectorAll("svg [data-agent]").length===4');
  const boxes=await evaluate('[...document.querySelectorAll("svg [data-agent],svg [data-artifact-marker]")].map(node=>{const b=node.getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height}})');
  for(let i=0;i<boxes.length;i++)for(let j=0;j<i;j++)assert.equal(overlap(boxes[i],boxes[j]),false);
  assert.equal(await evaluate('document.querySelector("[data-occupancy]").textContent'),'×4');
  for(const id of agents) {
    const point=await evaluate(`(()=>{const r=document.querySelector('svg [data-agent="${id}"]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
    await cdp.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point},sessionId);
    await cdp.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point},sessionId);
    assert.equal(await evaluate('members.at(-1)'),id);
    await evaluate(`(()=>{const node=document.querySelector('svg [data-agent="${id}"]');node.focus();node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()`);
    assert.equal(await evaluate('document.activeElement.dataset.agent'),id);
    assert.match(await evaluate('document.querySelector(".local").textContent'),/^2 nearby artifacts/);
  }
  await evaluate('document.querySelector("svg [data-occupancy]").dispatchEvent(new MouseEvent("click",{bubbles:true}))');
  assert.equal(await evaluate('inspections.at(-1).data.members.length'),4);
  assert.equal(await evaluate('inspections.at(-1).data.artifacts.length'),2);
  assert.deepEqual(await evaluate('frame'),frame,'display offsets must not change agent positions or world state');
  await evaluate('window.previous=document.querySelector("svg [data-agent]");viewer.update(frame)');
  assert.equal(await evaluate('previous===document.querySelector("svg [data-agent]")'),true);
  await cdp.send('Browser.close');
});

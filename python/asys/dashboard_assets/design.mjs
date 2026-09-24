/** Read presentation tokens once the selected design's CSS has loaded. */
export function readTheme(document=globalThis.document) {
  const css=document?.defaultView?.getComputedStyle(document.documentElement);
  const value=(name,fallback)=>css?.getPropertyValue('--'+name).trim()||fallback;
  const path=(name,fallback)=>value(name,fallback).replace(/^(["'])(.*)\1$/s,'$2');
  return Object.freeze({verde:value('verde','#3E7C5A'),blu:value('blu','#2A5CAA'),rosso:value('rosso','#D8402E'),
    giallo:value('giallo','#EFB02C'),goal:value('goal','#167D9A'),senate:value('senate','#8954A6'),swarm:value('swarm','#C26926'),
    carta:value('carta','#EFE7D6'),inchiostro:value('inchiostro','#201A12'),campo:value('campo','#E1D9C8'),rail:value('rail','#928B7E'),
    quiet:value('quiet','#6E675C'),font:value('font','"Helvetica Neue",Helvetica,Arial,sans-serif'),
    taskPath:path('task-shape','M0 1L1 1L.5 0Z'),gatewayPath:path('gateway-shape','M0 0L1 0L.5 1Z')});
}

export function markAttributes(theme, {x=0,y=0,width=24,height=21,gateway=false}={}) {
  return {d:gateway?theme.gatewayPath:theme.taskPath,transform:`translate(${x},${y}) scale(${width},${height})`,
    'vector-effect':'non-scaling-stroke'};
}

// Test endpoint using the unchanged Cyclo Provider implementation/transport.
import { runPassthrough } from './main.mjs';
import { PI_INFERENCE_FORMAT } from '@cyclo/provider/protocol';
import { createResourceExhaustedError } from '@cyclo/provider/errors';
let cancellations = 0;
const model = id => ({id, displayName:id, inferenceFormat:PI_INFERENCE_FORMAT,
  contextWindowTokens:100000n, maxOutputTokens:8000n,
  capabilities:{inputModalities:[1],outputModalities:[1]}});
await runPassthrough({createUpstream:async()=>({
  client:{
    async listModels(){return {models:[model('a/model'),model('b/model')]};},
    async *infer(request, options){
      if(request.model==='a/model')throw createResourceExhaustedError(new Date(Date.now()+60000));
      if(request.payload==='cancellations'){yield {payload:String(cancellations)};return;}
      yield {payload:request.payload};
      if(request.payload==='wait'){
        await new Promise(resolve=>{
          if(options.signal.aborted){cancellations++;resolve();return;}
          options.signal.addEventListener('abort',()=>{cancellations++;resolve();},{once:true});
        });return;
      }
      yield {payload:process.argv[2] ?? 'original'};
    },
  },
  callOptions:signal=>({signal}),
})});

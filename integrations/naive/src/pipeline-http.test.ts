import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {WorkflowStore} from './analysis-store.js';
import {pipelineRequest} from './pipeline-http.js';
test('dashboard feed reports real stages and costs while hiding local source and rendered paths',async()=>{
 const folder=await mkdtemp(tmpdir()+'/playlattice-pipeline-');const store=new WorkflowStore(':memory:');let body='';
 try{
  store.save({job_id:'j',game_id:'g',state:'succeeded',month:new Date().toISOString().slice(0,7),reserved:1234,presentation:{path:'/private/render.mp4',receipt_path:'/private/receipt.json',preview_uri:'http://127.0.0.1:8011/previews/j',quiz_count:2}});
  await writeFile(folder+'/overnight-state.json',JSON.stringify({status:'running',items:[{id:'v',name:'video',path:'/private/source.mp4',sha256:'hash',stage:'analysis'}]}));
  const handled=await pipelineRequest({method:'GET',url:'/pipeline'} as IncomingMessage,{end:(text:string)=>{body=text;}} as unknown as ServerResponse,store,folder+'/');
  assert.equal(handled,true);assert.ok(!body.includes('/private/'));const r=JSON.parse(body);assert.equal(r.budget.accounted_micro_usd,1234);assert.equal(r.batch.items[0].stage,'analysis');assert.equal(r.crew.length,6);
 }finally{store.close();await rm(folder,{recursive:true,force:true});}
});

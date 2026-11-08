import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { VettaClient } from '@usenaive-sdk/managed-agents';
import { WorkflowStore } from './analysis-store.js';
import { createAnalysisEngine } from './analysis-engine.js';
import { buildEvidence, snapshotId } from './analysis-contract.js';
import { crewMembers } from './crew.js';
function fixture() {
 const store=new WorkflowStore(':memory:');const hash='a'.repeat(64);
 const job={job_id:'j',game_id:'g',status:'succeeded',report:{source_sha256:hash}};
 const clip={clip_id:'c',game_id:'g',job_id:'j',source_sha256:hash,shot_id:'0',source_start_ms:0,source_end_ms:2000,qa_status:'passed',decoded:true,uri:'/api/media/registered'};
 const evidence=buildEvidence('g',job,[clip]);
 const r:any={job_id:'j',game_id:'g',state:'submitting',month:'2026-11',created_ms:Date.now(),reserved:1_000_000,key:'stable-key',evidence,snapshot_id:snapshotId(evidence)};store.reserve(r);
 const root:any={id:'root',status:'idle',stop_reason:'end_turn',consumed_micro_usd:100,structured_output:{job_id:'j',source_sha256:hash,clips:[{clip_id:'c',caption:'Basketball gameplay.',reason:'Source-linked clip'}],warnings:[],published:false,identity_verified:false,visual_input:false}};
 const children:any[]=crewMembers.map((a,i)=>({id:'child'+i,agent_id:a.id,status:'idle',stop_reason:'end_turn',consumed_micro_usd:10}));
 const creates:any[]=[];const cancelled:string[]=[];
 const sessions:any={create:async(body:any,options:any)=>{creates.push({body:structuredClone(body),options});return {id:'root'};},get:async()=>root,threads:async()=>({data:children,has_more:false}),cancel:async(id:string)=>{cancelled.push(id);if(id==='root')root.status='cancelled';else children.find(t=>t.id===id).status='cancelled';}};
 const read=async(path:string)=>path==='/api/jobs/j'?job:path==='/api/games/g/jobs'?{items:[job]}:{items:[clip]};
 const engine=createAnalysisEngine({client:{sessions} as Pick<VettaClient,'sessions'>,store,read,coordinatorVersion:5});
 return {store,r,root,children,creates,cancelled,sessions,engine,job,clip};
}
test('completion delivers a source-bound caption package only after all five specialists finish',async()=>{
 const f=fixture();try{await f.engine.advance(f.r);await f.engine.advance(f.r);const saved=f.store.get('j');assert.equal(saved.state,'succeeded');assert.equal(saved.reserved,150);assert.equal(saved.members.length,5);assert.equal(saved.output.clips[0].uri,'/api/media/registered');assert.equal(f.creates.length,1);}finally{f.store.close();}
});
test('ambiguous create replay uses the identical persisted body and key',async()=>{
 const f=fixture();try{const create=f.sessions.create;let fail=true;f.sessions.create=async(body:any,opts:any)=>{if(fail){fail=false;f.creates.push({body:structuredClone(body),options:opts});throw new Error('network timeout');}return create(body,opts);};await assert.rejects(f.engine.advance(f.r));await f.engine.advance(f.store.get('j'));assert.deepEqual(f.creates[0],f.creates[1]);assert.equal(f.store.get('j').reserved,1_000_000);}finally{f.store.close();}
});
test('waiting for delegation is not completion',async()=>{
 const f=fixture();try{await f.engine.advance(f.r);f.root.stop_reason='awaiting_delegation';await f.engine.advance(f.r);assert.equal(f.store.get('j').state,'running');assert.equal(f.store.get('j').output,undefined);}finally{f.store.close();}
});
test('superseding analysis cancels root and active child sessions',async()=>{
 const f=fixture();try{await f.engine.advance(f.r);f.job.status='cancelled';for(const c of f.children)c.status='running';await f.engine.advance(f.r);assert.equal(f.cancelled.length,6);assert.equal(f.store.get('j').state,'cancelled');assert.equal(f.store.get('j').output,null);}finally{f.store.close();}
});
test('cancellation before any submission does not start paid work',async()=>{
 const f=fixture();try{f.r.cancel_requested=true;await f.engine.advance(f.r);assert.equal(f.creates.length,0);assert.equal(f.store.get('j').reserved,0);}finally{f.store.close();}
});
test('late cancellation wins over remote success',async()=>{
 const f=fixture();try{await f.engine.advance(f.r);f.store.save({...f.r,cancel_requested:true});await f.engine.advance(f.r);assert.notEqual(f.store.get('j').state,'succeeded');assert.equal(f.store.get('j').output,null);}finally{f.store.close();}
});
test('remote read failure retains session and reservation for restart',async()=>{
 const f=fixture();try{await f.engine.advance(f.r);f.sessions.get=async()=>{throw new Error('capacity');};await assert.rejects(f.engine.advance(f.r));assert.equal(f.store.get('j').state,'running');assert.equal(f.store.get('j').session_id,'root');assert.equal(f.store.get('j').reserved,1_000_000);assert.equal(f.creates.length,1);}finally{f.store.close();}
});
test('timeout cancels remote work even if the local API is unavailable',async()=>{
 const f=fixture();try{
  await f.engine.advance(f.r);f.r.created_ms=Date.now()-16*60*1000;
  const engine=createAnalysisEngine({client:{sessions:f.sessions} as Pick<VettaClient,'sessions'>,store:f.store,read:async()=>{throw new Error('local offline');},coordinatorVersion:5});
  await engine.advance(f.r);assert.equal(f.store.get('j').state,'cancelled');assert.ok(f.cancelled.includes('root'));
 }finally{f.store.close();}
});

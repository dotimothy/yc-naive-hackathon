import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {WorkflowStore} from './analysis-store.js';
import {cancelBatch,batchCancelled} from './batch-control.js';
test('cancel preserves completed work, disables publishing, cancels queued and active work, and survives repeated requests',async()=>{
 const folder=await mkdtemp(tmpdir()+'/batch-control-')+'/';const store=new WorkflowStore(':memory:');const calls:string[]=[];
 try{
  await writeFile(folder+'overnight-state.json',JSON.stringify({id:'batch',items:[{id:'done',status:'posted',job_id:'done'},{id:'q',status:'queued'},{id:'active',status:'running',job_id:'active'}]}));
  await writeFile(folder+'publishing-policy.json',JSON.stringify({enabled:true}));
  store.save({job_id:'active',game_id:'g',state:'running',month:'2026-11',reserved:100});
  await assert.rejects(cancelBatch(folder,store,'old',async()=>{}),/Batch changed/);
  assert.equal(batchCancelled(folder,'batch'),false);
  const result=await cancelBatch(folder,store,'batch',async id=>{calls.push(id);});
  assert.deepEqual(result.analysis_cancel_errors,[]);assert.deepEqual(calls,['active']);
  assert.equal(batchCancelled(folder,'batch'),true);assert.equal(batchCancelled(folder,'new-batch'),false);
  const batch=JSON.parse(await readFile(folder+'overnight-state.json','utf8'));
  assert.equal(batch.status,'cancelled');assert.deepEqual(batch.counts,{posted:1,cancelled:2});
  assert.equal(JSON.parse(await readFile(folder+'publishing-policy.json','utf8')).enabled,false);
  assert.equal(store.get('active').cancel_requested,true);assert.equal(store.get('active').publication.cancel_requested,true);
  await cancelBatch(folder,store,'batch',async()=>{throw Error('should not retry');});
 }finally{store.close();await rm(folder,{recursive:true,force:true});}
});
test('failed active cancellation remains stopped and reports the unresolved job',async()=>{
 const folder=await mkdtemp(tmpdir()+'/batch-control-')+'/';const store=new WorkflowStore(':memory:');
 try{
  await writeFile(folder+'overnight-state.json',JSON.stringify({id:'batch',items:[{id:'v',status:'running',job_id:'j'}]}));
  await writeFile(folder+'publishing-policy.json',JSON.stringify({enabled:true}));
  const r=await cancelBatch(folder,store,'batch',async()=>{throw Error('offline');});
  assert.deepEqual(r.analysis_cancel_errors,['v']);assert.equal(batchCancelled(folder,'batch'),true);
  assert.equal(JSON.parse(await readFile(folder+'publishing-policy.json','utf8')).enabled,false);
 }finally{store.close();await rm(folder,{recursive:true,force:true});}
});
import {deleteCancelledItem,deleteCancelledItems,deletedItems} from './batch-control.js';
import {pipelineRequest} from './pipeline-http.js';
import type {IncomingMessage,ServerResponse} from 'node:http';
test('only cancelled entries can be deleted; deletion survives cancellation and hides them from the feed',async()=>{
 const folder=await mkdtemp(tmpdir()+'/batch-delete-')+'/';const store=new WorkflowStore(':memory:');
 try{
  await writeFile(folder+'overnight-state.json',JSON.stringify({id:'b',total:3,items:[{id:'c',status:'cancelled',path:'/private/a.mp4'},{id:'p',status:'posted'},{id:'q',status:'queued'}]}));
  await writeFile(folder+'publishing-policy.json',JSON.stringify({enabled:false}));
  await assert.rejects(deleteCancelledItem(folder,'b','p'),/Only cancelled/);
  await assert.rejects(deleteCancelledItem(folder,'b','q'),/Only cancelled/);
  await assert.rejects(deleteCancelledItem(folder,'old','c'),/Batch changed/);
  await deleteCancelledItem(folder,'b','c');assert.deepEqual([...deletedItems(folder,'b')],['c']);
  await cancelBatch(folder,store,'b',async()=>{});assert.deepEqual([...deletedItems(folder,'b')],['c'],'cancel keeps deletions');
  await deleteCancelledItem(folder,'b','q');
  let body='';await pipelineRequest({method:'GET',url:'/pipeline'} as IncomingMessage,{end:(t:string)=>{body=t;}} as unknown as ServerResponse,store,folder);
  const r=JSON.parse(body);assert.deepEqual(r.batch.items.map((i:any)=>i.id),['p']);assert.equal(r.batch.total,1);assert.deepEqual(r.batch.counts,{posted:1});assert.equal(r.batch.deleted_count,2);
  const res:any={statusCode:200,end(t:string){body=t;}};
  await pipelineRequest({method:'POST',url:'/pipeline/items/delete?batch_id=b&item_id=p',headers:{}} as unknown as IncomingMessage,res,store,folder);
  assert.equal(res.statusCode,403);
 }finally{store.close();await rm(folder,{recursive:true,force:true});}
});
test('delete all cancelled removes only cancelled entries',async()=>{
 const folder=await mkdtemp(tmpdir()+'/batch-delete-all-')+'/';
 try{
  await writeFile(folder+'overnight-state.json',JSON.stringify({id:'b',items:[{id:'a',status:'cancelled'},{id:'b1',status:'failed'},{id:'c',status:'cancelled'},{id:'d',status:'posted'}]}));
  const r=await deleteCancelledItems(folder,'b','all');assert.deepEqual(r.deleted,['a','c']);
  assert.deepEqual([...deletedItems(folder,'b')].sort(),['a','c']);
  await assert.rejects(deleteCancelledItems(folder,'b',['a','d']),/Only cancelled/);assert.equal(deletedItems(folder,'b').size,2,'rejected request deletes nothing');
 }finally{await rm(folder,{recursive:true,force:true});}
});

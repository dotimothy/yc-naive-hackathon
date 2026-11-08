import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildEvidence, snapshotId, validatePackage, metadataCaption } from './analysis-contract.js';
import { WorkflowStore } from './analysis-store.js';
const hash='a'.repeat(64);
const job={game_id:'g',job_id:'j',status:'succeeded',report:{source_sha256:hash}};
const clip={game_id:'g',job_id:'j',clip_id:'c',source_sha256:hash,qa_status:'passed',decoded:true,shot_id:'0',source_start_ms:100,source_end_ms:2100,uri:'/private',track_id:'person',highlight_score:99};
const evidence=()=>buildEvidence('g',job,[clip]);
const output=()=>({job_id:'j',source_sha256:hash,clips:[{clip_id:'c',caption:'Basketball gameplay sequence.',reason:'Source-linked segment'}],warnings:[],published:false,identity_verified:false,visual_input:false});
test('evidence strips URLs, identities, scores and rejects foreign/stale/failed clips',()=>{
 const e=buildEvidence('g',job,[clip,{...clip,clip_id:'foreign',job_id:'old'},{...clip,clip_id:'bad',qa_status:'failed'}]);
 assert.equal(e.clips.length,1);assert.equal('uri' in e.clips[0],false);assert.equal('highlight_score' in e.clips[0],false);
 assert.equal(snapshotId(e),snapshotId(evidence()));
});
test('package requires source provenance and honest uncertainty',()=>{
 assert.doesNotThrow(()=>validatePackage(output(),evidence()));
 for(const patch of [{job_id:'other'},{source_sha256:'bad'},{published:true},{identity_verified:true},{visual_input:true}])assert.throws(()=>validatePackage({...output(),...patch},evidence()));
});
test('invented, duplicated, overlapping and oversized selections fail',()=>{
 assert.throws(()=>validatePackage({...output(),clips:[{...output().clips[0],clip_id:'fake'}]},evidence()));
 assert.throws(()=>validatePackage({...output(),clips:[...output().clips,...output().clips]},evidence()));
 const e=buildEvidence('g',job,[clip,{...clip,clip_id:'d',source_start_ms:200}]);
 assert.throws(()=>validatePackage({...output(),clips:[...output().clips,{...output().clips[0],clip_id:'d'}]},e),/overlap/);
 const long=buildEvidence('g',job,Array.from({length:5},(_,i)=>({...clip,clip_id:String(i),source_start_ms:i*16000,source_end_ms:i*16000+15000})));
 assert.throws(()=>validatePackage({...output(),clips:long.clips.map(c=>({...output().clips[0],clip_id:c.clip_id}))},long),/sixty seconds/);
});
test('durable reservation deduplicates jobs, enforces monthly cap and retains pending costs',()=>{
 const s=new WorkflowStore(':memory:');
 try {
  const row=(i:number)=>({job_id:String(i),game_id:'g',state:'submitting',month:'2026-11'});
  assert.equal(s.reserve(row(0)),true);assert.equal(s.reserve(row(0)),false);
  for(let i=1;i<9;i++)s.reserve(row(i));
  assert.throws(()=>s.reserve(row(9)),/allocation exhausted/);
  s.save({...s.get('0'),state:'succeeded',reserved:100});assert.throws(()=>s.reserve(row(9)),/allocation exhausted/);
  s.save({...s.get('1'),state:'succeeded',reserved:100});assert.equal(s.reserve(row(9)),true);
 }finally{s.close();}
});
test('cancellation cannot be lost by an older asynchronous writer',()=>{
 const s=new WorkflowStore(':memory:');
 try {
  s.reserve({job_id:'j',game_id:'g',state:'running',month:'2026-11'});
  const old=s.get('j');s.save({...old,cancel_requested:true});
  s.save({...old,state:'succeeded',output:{clips:[]},reserved:10});
  assert.equal(s.get('j').state,'cancelling');assert.equal(s.get('j').cancel_requested,true);assert.equal(s.get('j').output,null);assert.equal(s.get('j').reserved,1_000_000);
 }finally{s.close();}
});

test('metadata captions state duration and uncertainty without invented action',()=>{assert.equal(metadataCaption({source_start_ms:1003,source_end_ms:8256}),'Basketball gameplay · 7.3 seconds. Player identities unverified.');assert.throws(()=>metadataCaption({source_start_ms:2,source_end_ms:1}));});

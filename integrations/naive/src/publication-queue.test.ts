import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WorkflowStore} from './analysis-store.js';
import {publicationTick,type PublishingPolicy} from './publication-queue.js';
import {TIKTOK_PROFILE} from './tiktok-media.js';
import {indoorSourceHash} from './publish-contract.js';
const policy:PublishingPolicy={enabled:true,platform:'tiktok',visibility:'public',enabled_at:'2026-11-08T00:00:00Z'};
function store(){const s=new WorkflowStore(':memory:');s.save({job_id:'j',game_id:'g',state:'succeeded',month:'2026-11',reserved:30000,completed_at:'2026-11-08T01:00:00Z',output:{source_sha256:indoorSourceHash,clips:[{clip_id:'c'}]}});return s;}
test('completed eligible analysis automatically invokes one publisher and retains its receipt',async()=>{
 const s=store();try{let called=0;await publicationTick(s,policy,async id=>{called++;const r=s.get(id);r.publication={...r.publication,status:'posted',post_id:'post'};s.save(r);});assert.equal(called,1);assert.equal(s.get('j').publication.post_id,'post');await publicationTick(s,policy,async()=>{throw new Error('duplicate');});assert.equal(s.get('j').publication.status,'posted');}finally{s.close();}
});
test('disabled publishing, older jobs, wrong sources and cancelled delivery cannot submit',async()=>{
 const s=store();try{let called=0;const run=async()=>{called++;};await publicationTick(s,{...policy,enabled:false},run);await publicationTick(s,{...policy,enabled_at:'2026-11-09T00:00:00Z'},run);let r=s.get('j');r.output.source_sha256='unknown';s.save(r);await publicationTick(s,policy,run);r.output.source_sha256=indoorSourceHash;r.publication={cancel_requested:true};s.save(r);await publicationTick(s,policy,run);assert.equal(called,0);assert.equal(s.get('j').publication.status,'cancelled');}finally{s.close();}
});
test('explicit existing-job inclusion permits one format-corrected retest; terminal failure never loops',async()=>{
 const s=store();try{const p={...policy,enabled_at:'2026-11-09T00:00:00Z',include_job_ids:['j']};let calls=0;await publicationTick(s,p,async()=>{calls++;throw new Error('upstream');});assert.equal(s.get('j').publication.profile,TIKTOK_PROFILE);assert.equal(s.get('j').publication.status,'blocked');await publicationTick(s,p,async()=>{calls++;});assert.equal(calls,1);}finally{s.close();}
});
test('publication fee reservation is idempotent and cannot exceed the per-video budget',()=>{
 const s=store();try{s.reservePublication('j',TIKTOK_PROFILE);s.reservePublication('j',TIKTOK_PROFILE);assert.equal(s.get('j').reserved,43000);const r=s.get('j');r.reserved=999000;s.save(r);assert.throws(()=>s.reservePublication('j','another-profile'),/per-video/);assert.equal(s.get('j').reserved,999000);}finally{s.close();}
});

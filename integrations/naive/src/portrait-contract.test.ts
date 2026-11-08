import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateQuizSelection} from './clipper-quizzes.js';
import {byteRange} from './preview-http.js';
import {WorkflowStore} from './analysis-store.js';
const candidates=[{candidate_id:'q1',track_id:'1'},{candidate_id:'q2',track_id:'2'},{candidate_id:'same-track',track_id:'1'}];
test('clipper can select only grounded distinct questions for distinct tracks',()=>{
 assert.deepEqual(validateQuizSelection({selected_candidate_ids:['q1','q2']},candidates),['q1','q2']);
 for(const ids of [['invented'],['q1','q1'],['q1','same-track']])assert.throws(()=>validateQuizSelection({selected_candidate_ids:ids},candidates));
});
test('registered portrait previews support seeking without arbitrary or out-of-bounds ranges',()=>{
 assert.deepEqual(byteRange('bytes=20-39',100),{start:20,end:39,partial:true});
 assert.deepEqual(byteRange('bytes=-10',100),{start:90,end:99,partial:true});
 assert.deepEqual(byteRange('bytes=20-',100),{start:20,end:99,partial:true});
 for(const header of ['bytes=200-','bytes=30-20','bytes=-0','invalid','bytes=0-2,5-6'])assert.throws(()=>byteRange(header,100));
});
test('settled quiz usage releases unused allocation and a zero-cost receipt stays idempotent',()=>{
 const s=new WorkflowStore(':memory:');try{
  s.save({job_id:'j',game_id:'g',state:'succeeded',month:'2026-11',reserved:40000});
  s.reservePublication('j','quiz',150000);assert.equal(s.get('j').reserved,190000);
  s.settleReservation('j','quiz',1398);assert.equal(s.get('j').reserved,41398);
  s.settleReservation('j','quiz',0);s.reservePublication('j','quiz',150000);assert.equal(s.get('j').reserved,40000);
  assert.throws(()=>s.reservePublication('j','bad',-1));
 }finally{s.close();}
});

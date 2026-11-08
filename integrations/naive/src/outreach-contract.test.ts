import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateOutreach} from './outreach-contract.js';
const catalog=[{id:'PL-1',duration_ms:10000,post_url:'https://www.tiktok.com/@playlatticeai/video/1'}];
const base={intent:'recommend',reply:'Here is a source-backed video to explore.',video_ids:['PL-1'],start_ms:null,end_ms:null,track_id:null};
test('outreach accepts catalog recommendations and source-bounded edit proposals',()=>{
 assert.equal(validateOutreach(base,catalog).intent,'recommend');
 assert.equal(validateOutreach({...base,intent:'clip',start_ms:1000,end_ms:5000,track_id:'7~2'},catalog).track_id,'7~2');
});
test('outreach rejects invented videos, destinations, tracks and out-of-bounds edits',()=>{
 for(const patch of [{video_ids:['unknown']},{reply:'Visit https://phishing.example'},{intent:'clip',start_ms:0,end_ms:11000},{intent:'clip',start_ms:4000,end_ms:3000},{track_id:'player/7'}])assert.throws(()=>validateOutreach({...base,...patch},catalog));
});

test('recommendations never carry edit fields into the local executor',()=>{const result=validateOutreach({...base,start_ms:0,end_ms:0},catalog);assert.equal(result.intent,'recommend');assert.equal(result.start_ms,null);assert.equal(result.end_ms,null);});

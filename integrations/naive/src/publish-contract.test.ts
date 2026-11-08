import {test} from 'node:test';
import assert from 'node:assert/strict';
import {unlistedTestPost,indoorSourceHash,selectTestWorkflow} from './publish-contract.js';
const clip={clip_id:'clip',shot_id:'0',source_start_ms:1003,source_end_ms:8256,identity_verified:false as const};
test('test publication forces YouTube unlisted and attributed grounded captions',()=>{
 const p=unlistedTestPost('fil_test',clip,indoorSourceHash);
 assert.deepEqual(p.platforms,['youtube']);assert.equal(p.visibility,'unlisted');assert.match(p.content,/7.3 seconds/);assert.match(p.content,/CC BY 4.0/);assert.match(p.content,/Kazuhiro Yamada/);assert.doesNotMatch(p.content,/defensive rotation|possession|made basket/);
});
test('unknown source or unregistered file cannot be published by the test path',()=>{
 assert.throws(()=>unlistedTestPost('fil_test',clip,'unknown'));assert.throws(()=>unlistedTestPost('http://localhost/file',clip,indoorSourceHash));
});

test('newer completed performance work cannot replace the attributed test source',()=>{
 const intended={state:'succeeded',output:{source_sha256:indoorSourceHash,clips:[clip]}};
 const newer={state:'succeeded',output:{source_sha256:'different-source',clips:[clip]}};
 assert.equal(selectTestWorkflow([newer,intended]),intended);
 assert.equal(selectTestWorkflow([newer]),undefined);
});

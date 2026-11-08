import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {mailroomStatus} from './mailroom-status.js';
test('mailroom status exposes aggregates without customer data or bearer links',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'mail-status-'));
 try{
  const db=new DatabaseSync(join(folder,'mailroom.sqlite3'));
  db.exec("CREATE TABLE requests(id TEXT,status TEXT,created REAL,updated REAL,sender TEXT,value TEXT,token TEXT); CREATE TABLE outbox(status TEXT,sender TEXT,body TEXT); INSERT INTO requests VALUES('request1','ready',1,2,'private@example.com','secret body','bearer-token'); INSERT INTO outbox VALUES('sent','private@example.com','private reply');");db.close();
  const now=Date.now();await writeFile(join(folder,'status.json'),JSON.stringify({pid:process.pid,state:'idle',updated_at:new Date(now).toISOString(),last_success_at:new Date(now).toISOString()}));
  const result=await mailroomStatus(folder,now);assert.equal(result.worker,'running');assert.equal(result.requests.ready,1);assert.equal(result.replies.sent,1);assert.equal(result.recent.length,1);
  const body=JSON.stringify(result);for(const privateValue of ['private@example.com','secret body','bearer-token','private reply'])assert.ok(!body.includes(privateValue));
  assert.equal((await mailroomStatus(folder,now+300001)).worker,'stale');
  await writeFile(join(folder,'status.json'),JSON.stringify({pid:process.pid,state:'error',updated_at:new Date(now).toISOString(),error_code:429}));assert.equal((await mailroomStatus(folder,now)).worker,'error');
 }finally{await rm(folder,{recursive:true,force:true});}
});
test('missing heartbeat and database remain unknown rather than healthy',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'mail-status-'));try{const r=await mailroomStatus(folder);assert.equal(r.worker,'unknown');assert.equal(r.ledger,'unavailable');}finally{await rm(folder,{recursive:true,force:true});}
});

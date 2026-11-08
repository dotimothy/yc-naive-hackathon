import {readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
/** Aggregate operational data only: never return customer addresses, message text or preview tokens. */
export async function mailroomStatus(folder:string,now=Date.now()) {
 let heartbeat:any=null;try{heartbeat=JSON.parse(await readFile(join(folder,'status.json'),'utf8'));}catch{}
 const checked=typeof heartbeat?.updated_at==='string'?Date.parse(heartbeat.updated_at):NaN;
 let alive=false;if(Number.isSafeInteger(heartbeat?.pid)&&heartbeat.pid>0)try{process.kill(heartbeat.pid,0);alive=true;}catch{}
 const fresh=Number.isFinite(checked)&&now-checked>=0&&now-checked<300000;
 const worker=!heartbeat?'unknown':!alive?'stopped':!fresh?'stale':heartbeat.state==='error'?'error':heartbeat.state==='stopped'?'stopped':'running';
 const result:any={worker,updated_at:Number.isFinite(checked)?heartbeat.updated_at:null,last_success_at:typeof heartbeat?.last_success_at==='string'?heartbeat.last_success_at:null,error_code:typeof heartbeat?.error_code==='number'?heartbeat.error_code:null,requests:{},replies:{},recent:[],ledger:'unavailable'};
 const file=join(folder,'mailroom.sqlite3');if(!existsSync(file))return result;
 let db:DatabaseSync|undefined;
 try{db=new DatabaseSync(file,{readOnly:true});
  for(const [table,key] of [['requests','requests'],['outbox','replies']])for(const row of db.prepare(`SELECT status, COUNT(*) AS count FROM ${table} GROUP BY status`).all())result[key][String(row.status)]=Number(row.count);
  result.recent=db.prepare('SELECT id,status,created,updated FROM requests ORDER BY created DESC LIMIT 5').all();result.ledger='ok';
 }catch{result.ledger='unavailable';}finally{db?.close();}
 return result;
}

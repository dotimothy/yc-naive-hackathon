import type {DatabaseSync} from 'node:sqlite';
const prefix='task-budget:';
export function taskBudgetTotal(db:DatabaseSync,month=new Date().toISOString().slice(0,7)) {
 return Number(db.prepare("SELECT COALESCE(SUM(json_extract(value,'$.reserved')),0) AS used FROM settings WHERE key LIKE 'task-budget:%' AND json_extract(value,'$.month')=?").get(month)!.used);
}
export function reserveTask(db:DatabaseSync,id:string,purpose:string,budget:number) {
 if(!Number.isSafeInteger(budget)||budget<=0||budget>1000000)throw Error('Invalid standalone task cap');
 db.exec('BEGIN IMMEDIATE');try{
  const key=prefix+id,existing=db.prepare('SELECT value FROM settings WHERE key=?').get(key);
  if(existing){const r=JSON.parse(String(existing.value));if(r.purpose!==purpose||r.budget!==budget)throw Error('Task reservation differs');db.exec('COMMIT');return r;}
  const month=new Date().toISOString().slice(0,7);
  const used=Number(db.prepare('SELECT COALESCE(SUM(reserved),0) AS used FROM workflows WHERE month=?').get(month)!.used)+taskBudgetTotal(db,month);
  if(used+budget>9000000)throw Error('Integration monthly allocation exhausted');
  const r={id,purpose,month,budget,reserved:budget,status:'reserved'};db.prepare('INSERT INTO settings VALUES(?,?)').run(key,JSON.stringify(r));db.exec('COMMIT');return r;
 }catch(e){db.exec('ROLLBACK');throw e;}
}
export function settleTask(db:DatabaseSync,id:string,actual:number,sessionId:string) {
 db.exec('BEGIN IMMEDIATE');try{
  const key=prefix+id,row=db.prepare('SELECT value FROM settings WHERE key=?').get(key);if(!row)throw Error('Missing task reservation');const r=JSON.parse(String(row.value));
  if(!Number.isSafeInteger(actual)||actual<0||actual>r.reserved)throw Error('Task exceeded reserved cap');
  r.reserved=actual;r.session_id=sessionId;r.status='settled';db.prepare('UPDATE settings SET value=? WHERE key=?').run(JSON.stringify(r),key);db.exec('COMMIT');
 }catch(e){db.exec('ROLLBACK');throw e;}
}

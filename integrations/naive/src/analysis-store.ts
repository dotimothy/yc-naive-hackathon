import { DatabaseSync } from 'node:sqlite';
import {taskBudgetTotal} from './task-budget.js';
export class WorkflowStore {
 readonly db: DatabaseSync;
 constructor(path: string) {
  this.db=new DatabaseSync(path);
  this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
   CREATE TABLE IF NOT EXISTS workflows(job_id TEXT PRIMARY KEY,game_id TEXT NOT NULL,state TEXT NOT NULL,month TEXT NOT NULL,reserved INTEGER NOT NULL,record TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);`);
 }
 get(id:string): any { const row=this.db.prepare('SELECT record FROM workflows WHERE job_id=?').get(id);return row?JSON.parse(String(row.record)):null; }
 all(): any[] {return this.db.prepare('SELECT record FROM workflows ORDER BY rowid DESC').all().map(r=>JSON.parse(String(r.record)));}
 save(r:any) {
  if(this.get(r.job_id)?.cancel_requested) {r.cancel_requested=true;if(r.state==='succeeded'){r.state='cancelling';r.output=null;r.reserved=1_000_000;}}
  this.db.prepare('INSERT OR REPLACE INTO workflows VALUES(?,?,?,?,?,?)').run(r.job_id,r.game_id,r.state,r.month,r.reserved,JSON.stringify(r));}
 reserve(r:any) {
  this.db.exec('BEGIN IMMEDIATE');
  try {
   if(this.get(r.job_id)) {this.db.exec('COMMIT');return false;}
   const used=Number(this.db.prepare('SELECT COALESCE(SUM(reserved),0) AS used FROM workflows WHERE month=?').get(r.month)!.used)+taskBudgetTotal(this.db,r.month);
   // $9 leaves $1 of the approved $10 allocation for onboarding and diagnostics.
   if(used+1_000_000>9_000_000) throw new Error('Integration monthly allocation exhausted');
   this.save({...r,reserved:1_000_000});this.db.exec('COMMIT');return true;
  } catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 reservePublication(jobId:string,profile:string,fee=13000) {
  if(!Number.isSafeInteger(fee)||fee<=0)throw new Error('Invalid publishing reservation');
  this.db.exec('BEGIN IMMEDIATE');
  try {
   const r=this.get(jobId);if(!r||r.state!=='succeeded')throw new Error('Completed workflow required for publishing');
   r.publication_reservations ||= {};
   if(Object.hasOwn(r.publication_reservations,profile)){this.db.exec('COMMIT');return;}
   if(r.reserved+fee>1_000_000)throw new Error('Publishing exceeds the approved per-video cap');
   const used=Number(this.db.prepare('SELECT COALESCE(SUM(reserved),0) AS used FROM workflows WHERE month=?').get(r.month)!.used)+taskBudgetTotal(this.db,r.month);
   if(used+fee>9_000_000)throw new Error('Publishing exceeds the integration monthly allocation');
   r.reserved+=fee;r.publication_reservations[profile]=fee;this.save(r);this.db.exec('COMMIT');
  }catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 settleReservation(jobId:string,profile:string,actual:number) {
  if(!Number.isSafeInteger(actual)||actual<0)throw new Error('Invalid settled usage');
  this.db.exec('BEGIN IMMEDIATE');
  try {
   const r=this.get(jobId),held=r?.publication_reservations?.[profile];
   if(typeof held!=='number'||actual>held)throw new Error('Usage exceeds its reserved ceiling');
   r.reserved+=actual-held;r.publication_reservations[profile]=actual;this.save(r);this.db.exec('COMMIT');
  }catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 initialized() {return Boolean(this.db.prepare("SELECT value FROM settings WHERE key='initialized'").get());}
 initialize() {this.db.prepare("INSERT OR IGNORE INTO settings VALUES('initialized','true')").run();}
 close(){this.db.close();}
}

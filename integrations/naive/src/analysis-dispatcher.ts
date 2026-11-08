import { mkdir, readFile, open, unlink } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createServer } from 'node:http';
import { setTimeout } from 'node:timers/promises';
import { naiveClient, safeError } from './client.js';
import { buildEvidence, snapshotId } from './analysis-contract.js';
import { WorkflowStore } from './analysis-store.js';
import { createAnalysisEngine } from './analysis-engine.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const base=new URL(process.env.PLAYLATTICE_URL||'http://127.0.0.1:8000');
if(!['127.0.0.1','localhost','[::1]'].includes(base.hostname)||base.protocol!=='http:') throw new Error('Analysis API must be loopback HTTP');
const client=naiveClient();
async function read(path:string) {const r=await fetch(new URL(path,base),{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Object.assign(new Error(`Local API HTTP ${r.status}`),{status:r.status});return r.json();}
await mkdir(folder,{recursive:true});
// OS process lock: a crash leaves a PID that is checked on the next start.
const lockPath=folder+'analysis-dispatcher.lock';
try {const old=Number(await readFile(lockPath,'utf8'));try {process.kill(old,0);throw new Error('Analysis dispatcher already running');} catch(e){if((e as NodeJS.ErrnoException).code!=='ESRCH')throw e;}await unlink(lockPath);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
const lock=await open(lockPath,'wx',0o600);await lock.writeFile(String(process.pid));await lock.close();
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');
const config=JSON.parse(await readFile(folder+'crew-versions.json','utf8'));
if(!config.complete||config.restore_pending)throw new Error('Crew integration versions are not ready');
let stop=false;let busy=false;
const {advance}=createAnalysisEngine({client,store,read,coordinatorVersion:config.coordinator_version});
for(const signal of ['SIGINT','SIGTERM'] as const)process.on(signal,()=>{stop=true;});
async function tick(explicitGame?:string) {
 if(busy)return;busy=true;
 try {
  for(const r of store.all().filter(r=>['submitting','running','cancelling'].includes(r.state)))try{await advance(r);}catch(e){const current=store.get(r.job_id);current.last_error=safeError(e);current.last_checked_at=new Date().toISOString();store.save(current);console.error(JSON.stringify({job_id:r.job_id,...safeError(e)}));}
  if(stop)return;
  const games=await read('/api/games');
  const initial=!store.initialized();
  for(const game of games.items) {
   if(!initial&&explicitGame&&game.game_id!==explicitGame)continue;
   const jobs=await read(`/api/games/${encodeURIComponent(game.game_id)}/jobs`);const job=jobs.items.at(-1);
   if(!job||job.status!=='succeeded'||store.get(job.job_id))continue;
   const month=new Date().toISOString().slice(0,7);
   if(initial&&game.game_id!==explicitGame){store.save({job_id:job.job_id,game_id:game.game_id,state:'baseline_skipped',month,reserved:0});continue;}
   const clips=await read(`/api/games/${encodeURIComponent(game.game_id)}/clips`);
   const evidence=buildEvidence(game.game_id,job,clips.items);const hash=snapshotId(evidence);
   const r={job_id:job.job_id,game_id:game.game_id,state:'submitting',month,reserved:1_000_000,created_ms:Date.now(),evidence,snapshot_id:hash,key:'playlattice-crew-v1-'+job.job_id};
   if(!evidence.clips.length){store.save({...r,state:'empty',reserved:0,output:{clips:[],warnings:['No source-bound QA-passed clips']}});continue;}
   if(store.reserve(r))await advance(r);
  }
  store.initialize();
 }finally{busy=false;}
}
const server=createServer(async(req,res)=>{
 res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
 try {
  const url=new URL(req.url||'/','http://127.0.0.1');
  if(req.method==='GET'&&url.pathname==='/health'){res.end(JSON.stringify({status:'ok',busy,publishing:false}));return;}
  if(req.method==='GET'&&url.pathname==='/workflows'){res.end(JSON.stringify({items:store.all().filter(r=>r.state!=='baseline_skipped').map(({evidence,request,...r})=>r)}));return;}
  const match=url.pathname.match(/^\/workflows\/([a-zA-Z0-9_-]+)\/cancel$/);
  if(req.method==='POST'&&match&&req.headers['x-playlattice-local']==='1'&&!req.headers.origin){const r=store.get(match[1]);if(!r){res.statusCode=404;res.end('{}');return;}if(['submitting','running','cancelling'].includes(r.state)){r.cancel_requested=true;store.save(r);}res.end(JSON.stringify({job_id:r.job_id,cancel_requested:r.cancel_requested||false}));return;}
  res.statusCode=404;res.end('{}');
 }catch(e){res.statusCode=500;res.end(JSON.stringify(safeError(e)));}
});
const index=process.argv.indexOf('--game');const game=index>=0?process.argv[index+1]:undefined;
if(index>=0&&!game)throw new Error('--game requires an ID');
server.listen(Number(process.env.PLAYLATTICE_CREW_PORT||8011),'127.0.0.1');
console.log(JSON.stringify({event:'dispatcher_ready',port:Number(process.env.PLAYLATTICE_CREW_PORT||8011),game:game||'new_completed_analyses'}));
try {while(!stop){try{await tick(game);}catch(e){console.error(JSON.stringify(safeError(e)));}await setTimeout(5000);}}
finally{await new Promise<void>(resolve=>server.close(()=>resolve()));store.close();await unlink(lockPath);}

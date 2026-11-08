import {readFile,writeFile,rename,open,unlink} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {WorkflowStore} from './analysis-store.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');
const lockPath=folder+'overnight.lock';
try{const pid=Number(await readFile(lockPath,'utf8'));try{process.kill(pid,0);throw Error('Overnight queue already running');}catch(e){if((e as NodeJS.ErrnoException).code!=='ESRCH')throw e;}await unlink(lockPath);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
const lock=await open(lockPath,'wx',0o600);await lock.writeFile(String(process.pid));await lock.close();
let stopping=false;for(const sig of ['SIGTERM','SIGINT'] as const)process.on(sig,()=>{stopping=true;});
const batch=JSON.parse(await readFile(folder+'overnight-state.json','utf8'));
const save=async()=>{batch.updated_at=new Date().toISOString();batch.counts={};for(const r of batch.items)batch.counts[r.status]=(batch.counts[r.status]||0)+1;await writeFile(folder+'overnight-state.json.tmp',JSON.stringify(batch,null,2),{mode:0o600});await rename(folder+'overnight-state.json.tmp',folder+'overnight-state.json');};
const active=()=>!stopping&&Date.now()<Date.parse(batch.stop_at);
const api=async(path:string,init:RequestInit={})=>{const r=await fetch('http://127.0.0.1:8000'+path,{...init,signal:AbortSignal.timeout(120000)});if(!r.ok)throw Error(`Local API ${r.status}: ${(await r.text()).slice(0,180)}`);return r.json();};
const stage=async(row:any,name:string)=>{row.stage=name;row.status='running';await save();console.log(JSON.stringify({event:'overnight_stage',id:row.id,stage:name}));};
try{
 batch.status='running';await save();
 for(const row of batch.items){
  if(!active())break;
  if(['posted','skipped','failed','cancelled'].includes(row.status))continue;
  try{
   const accounted=store.all().filter(r=>r.month===new Date().toISOString().slice(0,7)).reduce((s,r)=>s+r.reserved,0);
   if(accounted+150000>9000000){batch.status='budget_limited';break;}
   await stage(row,'discovery');
   const bytes=await readFile(row.path);if(bytes.length>250*1024*1024||createHash('sha256').update(bytes).digest('hex')!==row.sha256)throw Error('Source integrity/size check failed');
   const games=(await api('/api/games')).items;
   let game=games.filter((g:any)=>g.source_sha256===row.sha256).at(-1);
   if(!game){
    await stage(row,'raw_clipping');
    const form=new FormData();form.append('video',new Blob([bytes],{type:'video/mp4'}),row.name);form.append('rights_confirmed','true');form.append('auto_analyze','true');
    game=await api('/api/games',{method:'POST',body:form});
   }
   row.game_id=game.game_id;
   let job=(await api(`/api/games/${game.game_id}/jobs`)).items.at(-1);
   if(!job){
    const used=store.all().filter(r=>r.month===new Date().toISOString().slice(0,7)).reduce((s,r)=>s+r.reserved,0);
    if(used+1000000>9000000){batch.status='budget_limited';break;}
    job=await api(`/api/games/${game.game_id}/analyze`,{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':'overnight-'+row.sha256},body:JSON.stringify({basketball:true,stride:5,ball_stride:3,replay_paced:false})});
   }
   row.job_id=job.job_id;await stage(row,'analysis');
   const analysisDeadline=Date.now()+30*60*1000;
   while(active()&&['queued','running','cancelling'].includes(job.status)&&Date.now()<analysisDeadline){await sleep(5000);job=await api('/api/jobs/'+job.job_id);}
   if(!active())break;
   if(job.status==='cancelled'){row.status='cancelled';row.error='Analysis cancelled by operator';await save();continue;}
   if(job.status!=='succeeded')throw Error('Analysis did not succeed: '+job.status);
   await stage(row,'crew');
   const deadline=Date.now()+20*60*1000;
   let flow=store.get(row.job_id);
   while(active()&&(!flow||['running','submitting','cancelling','baseline_skipped'].includes(flow.state))&&Date.now()<deadline){await sleep(5000);flow=store.get(row.job_id);}
   if(!active())break;
   if(!flow||flow.state!=='succeeded'||!flow.output?.clips?.length){row.status='skipped';row.error=flow?.error||'No eligible source-bound clips or completed crew package';await save();continue;}
   await stage(row,'final_clipping');
   const policy=JSON.parse(await readFile(folder+'publishing-policy.json','utf8'));
   policy.include_job_ids=[...new Set([...(policy.include_job_ids||[]),row.job_id])];
   await writeFile(folder+'publishing-policy.json.tmp',JSON.stringify(policy,null,2),{mode:0o600});await rename(folder+'publishing-policy.json.tmp',folder+'publishing-policy.json');
   const publishDeadline=Date.now()+15*60*1000;
   while(active()&&Date.now()<publishDeadline){
    flow=store.get(row.job_id);const pub=flow?.publication;
    if(flow?.presentation){row.preview_url=flow.presentation.preview_uri;row.stage='posting';}
    if(pub?.status==='posted'){row.status='posted';row.stage='posted';row.post_url=pub.url;row.completed_at=new Date().toISOString();break;}
    if(['failed','blocked','cancelled','cancellation_pending'].includes(pub?.status)){row.status=pub.status==='cancelled'?'cancelled':String(pub.error||'').startsWith('Skipped:')?'skipped':'failed';row.error=pub.error||pub.status;break;}
    await save();await sleep(5000);
   }
   if(active()&&row.status==='running'){row.status='failed';row.error='Publication timed out; inspect saved journal before retry';}
  }catch(e){row.status='failed';row.error=(e as Error).message;console.log(JSON.stringify({event:'overnight_item_failed',id:row.id,error:row.error}));}
  await save();
 }
 if(batch.status==='running')batch.status=active()?'completed':stopping?'stopped':'time_limited';
 await save();console.log(JSON.stringify({event:'overnight_finished',status:batch.status,counts:batch.counts}));
}finally{store.close();await unlink(lockPath);}

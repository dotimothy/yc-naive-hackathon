/** Bounded recovery of an upstream social-provider outage; reuses exact post keys/bodies. */
import {readFile,writeFile,rename,readdir,open,unlink} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {setTimeout as sleep} from 'node:timers/promises';
import {naiveClient,safeError} from './client.js';
import {WorkflowStore} from './analysis-store.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const root=fileURLToPath(new URL('../',import.meta.url));
const lockPath=folder+'provider-recovery.lock';
try{const pid=Number(await readFile(lockPath,'utf8'));try{process.kill(pid,0);throw Error('Provider recovery already running');}catch(e){if((e as NodeJS.ErrnoException).code!=='ESRCH')throw e;}await unlink(lockPath);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
const lock=await open(lockPath,'wx',0o600);await lock.writeFile(String(process.pid));await lock.close();
const read=async(name:string)=>JSON.parse(await readFile(folder+name,'utf8'));
const save=async(name:string,value:any)=>{await writeFile(folder+name+'.tmp',JSON.stringify(value,null,2),{mode:0o600});await rename(folder+name+'.tmp',folder+name);};
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');let stopped=false;
for(const sig of ['SIGINT','SIGTERM'] as const)process.on(sig,()=>{stopped=true;});
try{
 const policy=await read('publishing-policy.json');const end=Date.parse(policy.stop_at);
 if(!Number.isFinite(end))throw Error('Recovery requires a finite authorized posting deadline');
 const candidates=[];
 for(const file of await readdir(folder)){
  if(!file.startsWith('publish-public-tiktok-player-portrait-quiz-v1-')||!file.endsWith('.json'))continue;
  const r=await read(file);const flow=store.get(r.job_id);
  if(r.post_attempted&&!r.post_id&&!r.provider_error&&!r.status&&r.body&&r.key&&flow?.state==='succeeded'&&!flow.cancel_requested&&!flow.publication?.cancel_requested&&Date.now()-r.created_ms<23*3600000)candidates.push({file,state:r});
 }
 candidates.sort((a,b)=>b.state.created_ms-a.state.created_ms);
 if(!candidates.length)throw Error('No safely resumable saved post request');
 const probe=candidates[0];
 await save('provider-recovery-status.json',{status:'waiting',next_check_at:new Date(Date.now()+300000).toISOString(),stop_at:policy.stop_at,reason:'Naive social provider rejected its key',retained_requests:candidates.length});
 while(!stopped&&Date.now()+300000<end){
  // Wait in short increments so termination does not leave a sleeping orphan.
  for(let i=0;i<30&&!stopped;i++)await sleep(10000);
  if(stopped||Date.now()>=end)break;
  const latestPolicy=await read('publishing-policy.json');
  if(latestPolicy.stop_at!==policy.stop_at)throw Error('Posting authorization deadline changed; recovery stopped');
  if(store.get(probe.state.job_id)?.publication?.cancel_requested)throw Error('Recovery post was cancelled');
  try{
   const post=await naiveClient(probe.state.key).social.createPost(probe.state.identity,probe.state.body);
   probe.state.post_id=post.id;probe.state.status=post.status;await save(probe.file,probe.state);
   for(const {state:r} of candidates){const flow=store.get(r.job_id);if(!flow||flow.cancel_requested||flow.publication?.cancel_requested)continue;flow.publication={...flow.publication,status:'pending',error:null};store.save(flow);}
   const batch=await read('overnight-state.json');
   for(const row of batch.items){if(candidates.some(c=>c.state.job_id===row.job_id)&&['failed','running'].includes(row.status)){row.status='queued';delete row.error;}}
   batch.status='queued';batch.counts={};for(const r of batch.items)batch.counts[r.status]=(batch.counts[r.status]||0)+1;await save('overnight-state.json',batch);
   latestPolicy.enabled=true;delete latestPolicy.paused_reason;await save('publishing-policy.json',latestPolicy);
   const log=await open(folder+'overnight-overnight.log','a');
   const child=spawn(root+'node_modules/.bin/tsx',[root+'src/overnight.ts'],{cwd:root,detached:true,stdio:['ignore',log.fd,log.fd]});child.unref();await log.close();
   await save('provider-recovery-status.json',{status:'recovered',at:new Date().toISOString(),post_id:post.id,resumed_requests:candidates.length});
   console.log(JSON.stringify({event:'social_provider_recovered',post_id:post.id}));break;
  }catch(e){
   const error=safeError(e);await save('provider-recovery-status.json',{status:'provider_blocked',checked_at:new Date().toISOString(),next_check_at:new Date(Date.now()+300000).toISOString(),stop_at:policy.stop_at,error});console.log(JSON.stringify({event:'provider_recheck_failed',...error}));
   if((e as any).status!==501&&(e as any).status!==429&&(e as any).status<500)throw e;
  }
 }
}finally{store.close();await unlink(lockPath);}

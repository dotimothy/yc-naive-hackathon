import {mailroomStatus} from './mailroom-status.js';
import {resolve} from 'node:path';
import {cancelBatch,batchCancelled,deleteCancelledItems,deletedItems} from './batch-control.js';
import type {TeamActivity} from './team-activity.js';
import {taskBudgetTotal} from './task-budget.js';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {readFile,readdir} from 'node:fs/promises';
import {companyCrew} from './crew.js';
import type {WorkflowStore} from './analysis-store.js';
export async function pipelineRequest(req:IncomingMessage,res:ServerResponse,store:WorkflowStore,folder:string,activity?:TeamActivity) {
 const url=new URL(req.url||'/','http://127.0.0.1');
 if(req.method==='POST'&&url.pathname==='/pipeline/cancel'){
  if(req.headers['x-playlattice-control']!=='1'){res.statusCode=403;res.end(JSON.stringify({error:'Control header required'}));return true;}
  try{const result=await cancelBatch(folder,store,url.searchParams.get('batch_id')||'',async id=>{
   const r=await fetch(`http://127.0.0.1:8000/api/jobs/${encodeURIComponent(id)}/cancel`,{method:'POST',signal:AbortSignal.timeout(10000)});
   if(!r.ok&&r.status!==409)throw Error('Analysis cancellation failed');
  });res.end(JSON.stringify(result));}catch(e){res.statusCode=(e as any).status||500;res.end(JSON.stringify({error:(e as Error).message}));}return true;
 }
 if(req.method==='POST'&&url.pathname==='/pipeline/items/delete'){
  if(req.headers['x-playlattice-control']!=='1'){res.statusCode=403;res.end(JSON.stringify({error:'Control header required'}));return true;}
  try{res.end(JSON.stringify(await deleteCancelledItems(folder,url.searchParams.get('batch_id')||'',url.searchParams.get('all_cancelled')==='1'?'all':[url.searchParams.get('item_id')||''])));}
  catch(e){res.statusCode=(e as any).status||500;res.end(JSON.stringify({error:(e as Error).message}));}return true;
 }
 if(req.method!=='GET'||url.pathname!=='/pipeline')return false;
 const read=async(name:string)=>{try{return JSON.parse(await readFile(folder+name,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return null;throw e;}};
 const batch=await read('overnight-state.json');
 const cancelledJobs=new Set<string>(batch&&batchCancelled(folder,batch.id)?batch.items.map((i:any)=>i.job_id).filter(Boolean):[]);
 if(batch&&batchCancelled(folder,batch.id))batch.status='cancelled';
 if(batch){
  const cancelled=batchCancelled(folder,batch.id),deleted=deletedItems(folder,batch.id);
  batch.items=batch.items.filter((i:any)=>!deleted.has(i.id)).map(({path,sha256,...item}:any)=>cancelled&&['queued','running'].includes(item.status)?{...item,status:'cancelled'}:item);
  if(deleted.size){batch.deleted_count=deleted.size;if(typeof batch.total==='number')batch.total=Math.max(0,batch.total-deleted.size);batch.counts={};for(const i of batch.items)batch.counts[i.status]=(batch.counts[i.status]||0)+1;}
 }
 const records=store.all();
 if(activity)await Promise.race([activity.refresh(records),new Promise(r=>setTimeout(r,1500))]);
 const workflows=store.all().filter(r=>r.state!=='baseline_skipped').map(r=>({job_id:r.job_id,game_id:r.game_id,state:r.state,created_ms:r.created_ms,completed_at:r.completed_at,reserved:r.reserved,cost_micro_usd:r.cost_micro_usd,output:r.output,members:r.members,presentation:r.presentation?{preview_uri:r.presentation.preview_uri,track_count:r.presentation.track_count,quiz_count:r.presentation.quiz_count,quiz_music_missing:r.presentation.quiz_music_missing}:null,publication:r.publication?{status:r.publication.status,url:r.publication.url||r.publication.posted_url,post_id:r.publication.post_id,error:r.publication.error,requested_visibility:r.publication.requested_visibility,profile:r.publication.profile}:null}));
 let publisher='stopped';try{const pid=Number(await readFile(folder+'publish-watch.lock','utf8'));process.kill(pid,0);publisher='running';}catch{}
 let outreachSessions=0;let outreachState='not_configured';
 try{const rows=await Promise.all((await readdir(folder+'outreach-requests')).filter(f=>f.endsWith('.json')).map(f=>read('outreach-requests/'+f)));const latest=rows.filter(Boolean).sort((a,b)=>(b.created_ms||0)-(a.created_ms||0))[0];outreachSessions=rows.filter(r=>r?.session_id).length;outreachState=latest?.output?'completed':latest?.session_state||'configured';}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 const policy=await read('publishing-policy.json');
 res.end(JSON.stringify({updated_at:new Date().toISOString(),agentmail:await mailroomStatus(resolve(folder,'../agentmail')),batch,workflows,budget:{monthly_cap_micro_usd:9000000,per_video_cap_micro_usd:1000000,accounted_micro_usd:store.all().filter(r=>r.month===new Date().toISOString().slice(0,7)).reduce((s,r)=>s+r.reserved,0)+taskBudgetTotal(store.db)},services:{dispatcher:'ok',publisher,outreach:outreachState},crew:companyCrew.map(a=>({...a,role:a.name,...(a.name==='outreach'?{session_count:outreachSessions}:{})})),team_activity:activity?activity.snapshot(records,cancelledJobs):null,policy:policy?{enabled:policy.enabled&&(!policy.stop_at||Date.now()<Date.parse(policy.stop_at)),stop_at:policy.stop_at,paused_until:policy.paused_until,paused_reason:policy.paused_reason,platform:policy.platform,visibility:policy.visibility}:null}));return true;
}

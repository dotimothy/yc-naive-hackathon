import type {IncomingMessage,ServerResponse} from 'node:http';
import {readFile} from 'node:fs/promises';
import {crew,crewMembers} from './crew.js';
import type {WorkflowStore} from './analysis-store.js';
export async function pipelineRequest(req:IncomingMessage,res:ServerResponse,store:WorkflowStore,folder:string) {
 if(req.method!=='GET'||new URL(req.url||'/','http://127.0.0.1').pathname!=='/pipeline')return false;
 const read=async(name:string)=>{try{return JSON.parse(await readFile(folder+name,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return null;throw e;}};
 const batch=await read('overnight-state.json');
 if(batch)batch.items=batch.items.map(({path,sha256,...item}:any)=>item);
 const workflows=store.all().filter(r=>r.state!=='baseline_skipped').map(r=>({job_id:r.job_id,game_id:r.game_id,state:r.state,created_ms:r.created_ms,completed_at:r.completed_at,reserved:r.reserved,cost_micro_usd:r.cost_micro_usd,output:r.output,members:r.members,presentation:r.presentation?{preview_uri:r.presentation.preview_uri,track_count:r.presentation.track_count,quiz_count:r.presentation.quiz_count,quiz_music_missing:r.presentation.quiz_music_missing}:null,publication:r.publication?{status:r.publication.status,url:r.publication.url||r.publication.posted_url,post_id:r.publication.post_id,error:r.publication.error,requested_visibility:r.publication.requested_visibility,profile:r.publication.profile}:null}));
 let publisher='stopped';try{const pid=Number(await readFile(folder+'publish-watch.lock','utf8'));process.kill(pid,0);publisher='running';}catch{}
 const policy=await read('publishing-policy.json');
 res.end(JSON.stringify({updated_at:new Date().toISOString(),batch,workflows,budget:{monthly_cap_micro_usd:9000000,per_video_cap_micro_usd:1000000,accounted_micro_usd:store.all().filter(r=>r.month===new Date().toISOString().slice(0,7)).reduce((s,r)=>s+r.reserved,0)},services:{dispatcher:'ok',publisher},crew:[crew.coordinator,...crewMembers].map(a=>({...a,role:a.name})),policy:policy?{enabled:policy.enabled&&(!policy.stop_at||Date.now()<Date.parse(policy.stop_at)),stop_at:policy.stop_at,platform:policy.platform,visibility:policy.visibility}:null}));return true;
}

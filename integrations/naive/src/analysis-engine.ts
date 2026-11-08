import type { VettaClient } from '@usenaive-sdk/managed-agents';
import { crew, crewMembers } from './crew.js';
import { buildEvidence, packageSchema, snapshotId, validatePackage, metadataCaption } from './analysis-contract.js';
import type { WorkflowStore } from './analysis-store.js';

/** Deterministic local orchestration; remote sessions never execute local inference. */
export function createAnalysisEngine(deps: {client: Pick<VettaClient,'sessions'>;store:WorkflowStore;read:(path:string)=>Promise<any>;coordinatorVersion:number}) {
 const {client,store,read}=deps;
 const config={coordinator_version:deps.coordinatorVersion};
async function threads(id:string) {
 const all=[]; let after:string|undefined;
 for(let page=0;;page++) {
  if(page>=10)throw new Error('Thread pagination incomplete');
  const result=await client.sessions.threads(id,{limit:100,...(after?{after}:{})});all.push(...result.data);
  if(!result.has_more) return all;
  if(!result.next_cursor||result.next_cursor===after)throw new Error('Invalid thread cursor');after=result.next_cursor;
 }
}
async function cancel(r:any,reason:string) {
 r.cancel_requested=true;r.state='cancelling';r.error=reason;store.save(r);
 if(r.session_id) {
  await client.sessions.cancel(r.session_id);
  const children=await threads(r.session_id);
  for(const child of children)if(child.status==='running'||child.status==='queued'||child.status==='idle'&&child.stop_reason!=='end_turn')await client.sessions.cancel(child.id);
  const root=await client.sessions.get(r.session_id);
  const latest=await threads(r.session_id);
  if(root.status==='running'||root.status==='queued'||latest.some(t=>t.status==='running'||t.status==='queued'))return;
  r.reserved=root.consumed_micro_usd+latest.reduce((sum,t)=>sum+t.consumed_micro_usd,0);
 } else if(!r.submission_attempted) r.reserved=0;
 r.state='cancelled';r.output=null;store.save(r);
}
async function submit(r:any) {
 if(Date.now()-r.created_ms>23*60*60*1000)throw new Error('Ambiguous submission exceeded safe idempotency replay window; retained reservation');
 r.request ||= {agent_id:crew.coordinator.id,agent_version:config.coordinator_version,budget_micro_usd:1_000_000,output_schema:packageSchema,structured_output_required:true,
  metadata:{playlattice_job_id:r.job_id,playlattice_workflow:r.key},
  message:'Run the local-analysis integration with all five pinned crew members. Use only supplied metadata; no video was inspected. All sends must use wait:true. Select only existing nonoverlapping QA-passed clips. Captions must describe generic basketball gameplay, never specific outcomes or player identities. Preserve uncertainty. Return the structured package using submit_output. Evidence: '+JSON.stringify(r.evidence),
 };
 r.state='submitting';r.submission_attempted=true;store.save(r);
 const session=await client.sessions.create(r.request,{idempotencyKey:r.key});
 r.session_id=session.id;r.state='running';store.save(r);
 console.log(JSON.stringify({event:'crew_started',job_id:r.job_id,session_id:session.id}));
}
async function advance(r:any) {
 if(r.cancel_requested){if(!r.session_id&&r.submission_attempted)await submit(r);await cancel(r,'Cancellation requested');return;}
 if(Date.now()-r.created_ms>15*60*1000){if(!r.session_id&&r.submission_attempted)await submit(r);await cancel(r,'Crew timeout');return;}
 const job=await read(`/api/jobs/${encodeURIComponent(r.job_id)}`).catch(e=>{if(e.status===404)return {status:'missing'};throw e;});
 if(job.status==='missing'){if(r.submission_attempted&&!r.session_id)await submit(r);await cancel(r,'Source analysis removed');return;}
 const jobs=await read(`/api/games/${encodeURIComponent(r.game_id)}/jobs`);
 if(job.status!=='succeeded'||jobs.items.at(-1)?.job_id!==r.job_id){if(r.submission_attempted&&!r.session_id)await submit(r);r.state_before_cancel=r.state;await cancel(r,'Analysis cancelled or superseded');return;}
 if(!r.session_id){await submit(r);return;}
 const session=await client.sessions.get(r.session_id);
 delete r.last_error;r.last_checked_at=new Date().toISOString();store.save(r);
 if(session.status==='running'||session.status==='queued'||session.status==='idle'&&session.stop_reason==='awaiting_delegation')return;
 if(session.status!=='idle'||session.stop_reason!=='end_turn'){await cancel(r,`Crew ended with ${session.status}/${session.stop_reason}`);return;}
 const children=await threads(session.id);
 const completed=new Set(children.filter(t=>t.status==='idle'&&t.stop_reason==='end_turn').map(t=>t.agent_id));
 if(crewMembers.some(a=>!completed.has(a.id))) {await cancel(r,'Not all five existing specialists completed');return;}
 try {r.output=validatePackage(session.structured_output,r.evidence);}catch(e){await cancel(r,(e as Error).message);return;}
 // Recheck local provenance after remote reads and before making results visible.
 const current=await read(`/api/games/${encodeURIComponent(r.game_id)}/jobs`);
 const clips=await read(`/api/games/${encodeURIComponent(r.game_id)}/clips`);
 const currentJob=current.items.at(-1);
 if(!currentJob||snapshotId(buildEvidence(r.game_id,currentJob,clips.items))!==r.snapshot_id){await cancel(r,'Analysis evidence changed');return;}
 if(store.get(r.job_id)?.cancel_requested){await cancel(r,'Cancellation won final commit');return;}
 r.state='succeeded';r.completed_at=new Date().toISOString();
 r.reserved=session.consumed_micro_usd+children.reduce((sum,t)=>sum+t.consumed_micro_usd,0);
 r.cost_micro_usd=r.reserved;r.members=children.map(t=>({session_id:t.id,agent_id:t.agent_id,status:t.status,stop_reason:t.stop_reason,cost_micro_usd:t.consumed_micro_usd}));
 r.output.clips=r.output.clips.map((c:any)=>({...c,...r.evidence.clips.find((e:any)=>e.clip_id===c.clip_id),uri:clips.items.find((e:any)=>e.clip_id===c.clip_id)?.uri,caption:metadataCaption(r.evidence.clips.find((e:any)=>e.clip_id===c.clip_id))}));
 r.caption_policy='metadata_only';
 store.save(r);console.log(JSON.stringify({event:'crew_completed',job_id:r.job_id,clips:r.output.clips.length,cost_micro_usd:r.cost_micro_usd}));
}

return {advance,cancel};
}

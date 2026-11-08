import type {WorkflowStore} from './analysis-store.js';
import {PORTRAIT_PROFILE} from './portrait-export.js';
import {publishingSource} from './publishing-sources.js';
export type PublishingPolicy={enabled:boolean;platform:'tiktok';visibility:'public';enabled_at:string;include_job_ids?:string[];stop_at?:string};
/** One publisher owns delivery; role agents never race independent social posts. */
export async function publicationTick(store:WorkflowStore,policy:PublishingPolicy,run:(id:string)=>Promise<void>) {
 if(!policy.enabled)return;
 if(policy.stop_at&&(!Number.isFinite(Date.parse(policy.stop_at))||Date.now()>=Date.parse(policy.stop_at)))return;
 if(policy.platform!=='tiktok'||policy.visibility!=='public')throw new Error('Unexpected autonomous publishing scope');
 const start=Date.parse(policy.enabled_at);if(!Number.isFinite(start))throw new Error('Invalid publishing activation time');
 for(const r of store.all()) {
  if(r.state!=='succeeded'||!r.output?.clips?.length||!publishingSource(r.output.source_sha256))continue;
  if(!policy.include_job_ids?.includes(r.job_id)&&!(Date.parse(r.completed_at)>start))continue;
  if(r.publication?.profile===PORTRAIT_PROFILE&&['posted','failed','blocked','cancelled','cancellation_pending'].includes(r.publication.status))continue;
  if(r.publication?.cancel_requested){r.publication.status='cancelled';store.save(r);continue;}
  r.publication={...r.publication,profile:PORTRAIT_PROFILE,platform:'tiktok',requested_visibility:'public',status:'preparing',error:null,previous_attempts:r.publication?.attempts};store.save(r);
  try {
   await run(r.job_id);
   const current=store.get(r.job_id);
   if(!['posted','pending','cancellation_pending'].includes(current.publication?.status)){current.publication={...current.publication,status:current.publication?.cancel_requested?'cancellation_pending':'blocked',error:'Publisher ended without a confirmed receipt; inspect its saved journal'};store.save(current);}
  }catch(e){const current=store.get(r.job_id);if(!['posted','pending','cancellation_pending'].includes(current.publication?.status)){current.publication={...current.publication,status:current.publication?.cancel_requested?'cancellation_pending':current.publication?.status==='failed'?'failed':'blocked',error:e instanceof Error?e.message:'Publisher failed'};store.save(current);}}
  return; // Bound one delivery at a time; next tick picks the next eligible workflow.
 }
}

import {readFile,writeFile,rename} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import type {WorkflowStore} from './analysis-store.js';
export function batchCancelled(folder:string,id:string) {
 try{return JSON.parse(readFileSync(folder+'batch-control.json','utf8')).cancelled_batch_id===id;}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return false;throw e;}
}
export async function cancelBatch(folder:string,store:WorkflowStore,id:string,cancelAnalysis:(id:string)=>Promise<void>) {
 const read=async(name:string)=>JSON.parse(await readFile(folder+name,'utf8'));
 const save=async(name:string,value:unknown)=>{const path=folder+name;await writeFile(path+'.control.tmp',JSON.stringify(value,null,2),{mode:0o600});await rename(path+'.control.tmp',path);};
 const batch=await read('overnight-state.json');
 if(!id||batch.id!==id)throw Object.assign(new Error('Batch changed; refresh before cancelling'),{status:409});
 const at=new Date().toISOString();
 await save('batch-control.json',{...await readControl(folder),cancelled_batch_id:id,cancelled_at:at});
 const policy=await read('publishing-policy.json');policy.enabled=false;policy.paused_reason='Batch cancelled by operator';await save('publishing-policy.json',policy);
 const errors:string[]=[];
 for(const item of batch.items){
  if(['posted','skipped','failed','cancelled','completed','succeeded'].includes(item.status))continue;
  const flow=item.job_id?store.get(item.job_id):null;
  if(flow){
   if(['submitting','running','cancelling'].includes(flow.state))flow.cancel_requested=true;
   if(flow.publication?.status!=='posted')flow.publication={...flow.publication,cancel_requested:true,status:flow.publication?.post_id?'cancellation_pending':'cancelled'};
   store.save(flow);
  }
  if(item.job_id){try{await cancelAnalysis(item.job_id);}catch{errors.push(item.id);}}
  item.status='cancelled';item.cancelled_at=at;
 }
 batch.status='cancelled';batch.cancelled_at=at;batch.updated_at=at;batch.counts={};
 for(const item of batch.items)batch.counts[item.status]=(batch.counts[item.status]||0)+1;
 await save('overnight-state.json',batch);
 return {status:'cancelled',batch_id:id,analysis_cancel_errors:errors};
}

type Control={cancelled_batch_id?:string;cancelled_at?:string;deleted_items?:Record<string,string[]>};
async function readControl(folder:string):Promise<Control>{
 try{return JSON.parse(await readFile(folder+'batch-control.json','utf8'));}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return {};throw e;}
}
/** Item IDs the operator deleted from this batch's dashboard list. */
export function deletedItems(folder:string,batchId:string):Set<string>{
 try{return new Set(JSON.parse(readFileSync(folder+'batch-control.json','utf8')).deleted_items?.[batchId]||[]);}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return new Set();throw e;}
}
/** Deletes a cancelled video's entry from the list. Only the entry is hidden: source files, games,
    analyses and workflow records are kept. The queue owns overnight-state.json and rewrites it, so the
    deletion lives in batch-control.json. */
export async function deleteCancelledItem(folder:string,batchId:string,itemId:string){
 const r=await deleteCancelledItems(folder,batchId,[itemId]);return {deleted:itemId,batch_id:r.batch_id};
}
/** `'all'` deletes every cancelled entry in the batch; explicit IDs must all be cancelled. */
export async function deleteCancelledItems(folder:string,batchId:string,ids:string[]|'all'){
 const batch=JSON.parse(await readFile(folder+'overnight-state.json','utf8'));
 if(!batchId||batch.id!==batchId)throw Object.assign(new Error('Batch changed; refresh before deleting'),{status:409});
 const control=await readControl(folder);
 const isCancelled=(item:any)=>item.status==='cancelled'||control.cancelled_batch_id===batchId&&['queued','running'].includes(item.status);
 let targets:string[];
 if(ids==='all')targets=batch.items.filter(isCancelled).map((i:any)=>i.id);
 else{
  if(!ids.length||ids.some(id=>!id))throw Object.assign(new Error('No video entry given'),{status:400});
  for(const id of ids){const item=batch.items.find((i:any)=>i.id===id);
   if(!item)throw Object.assign(new Error('Video entry not found'),{status:404});
   if(!isCancelled(item))throw Object.assign(new Error('Only cancelled videos can be deleted'),{status:409});}
  targets=ids;
 }
 const list=new Set(control.deleted_items?.[batchId]||[]);for(const id of targets)list.add(id);
 const next={...control,deleted_items:{...control.deleted_items,[batchId]:[...list]}};
 await writeFile(folder+'batch-control.json.control.tmp',JSON.stringify(next,null,2),{mode:0o600});await rename(folder+'batch-control.json.control.tmp',folder+'batch-control.json');
 return {deleted:targets,batch_id:batchId};
}

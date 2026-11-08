/** Restartable bounded tick. launchd may invoke --once every 30 minutes; --watch is optional. */
import {readFile,writeFile,mkdir,rename,open,unlink,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {setTimeout as sleep} from 'node:timers/promises';
import {WorkflowStore} from './analysis-store.js';
import {reserveTask,settleTask,taskBudgetTotal} from './task-budget.js';
import {naiveClient,safeError} from './client.js';
import {crew} from './crew.js';
import {DEFAULT_DAILY_POLICY,validateDailyPolicy,dayKey,youtubeId,eligibleCandidate,hasDailySlot,nextRoundBudget} from './daily-discovery-core.js';
const root=fileURLToPath(new URL('../../../',import.meta.url)),folder=join(root,'data/naive'),statePath=join(folder,'daily-discovery-state.json'),policyPath=join(folder,'daily-discovery-policy.json');
const run=promisify(execFile);
const read=async(p:string,fallback:any)=>{try{return JSON.parse(await readFile(p,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return fallback;throw e;}};
const atomic=async(p:string,x:any)=>{await writeFile(p+'.tmp',JSON.stringify(x,null,2)+'\n',{mode:0o600});await rename(p+'.tmp',p);};
const digest=(bytes:Uint8Array)=>createHash('sha256').update(bytes).digest('hex');
/** Mirror must independently identify the identical original source and reusable license. */
async function commonsMirror(evidence:string,id:string,dest:string,maxBytes:number){
 const u=new URL(evidence);if(u.protocol!=='https:'||u.hostname!=='commons.wikimedia.org'||!u.pathname.startsWith('/wiki/File:'))throw Error('No matching authorized public mirror');
 const title=decodeURIComponent(u.pathname.slice('/wiki/'.length));
 const api=new URL('https://commons.wikimedia.org/w/api.php');api.search=new URLSearchParams({action:'query',format:'json',prop:'imageinfo',iiprop:'url|extmetadata',titles:title}).toString();
 const response=await fetch(api,{signal:AbortSignal.timeout(20000)});if(!response.ok)throw Error('Commons metadata unavailable');
 const pages=(await response.json() as any).query?.pages||{};const info=(Object.values(pages)[0] as any)?.imageinfo?.[0];const metadata=info?.extmetadata||{};
 const values=JSON.stringify(metadata);const license=String(metadata.LicenseShortName?.value||'');
 if(!values.includes(id)||!/(CC.?BY|CC0|Creative Commons)/i.test(license))throw Error('Commons metadata does not match source ID and explicit reusable license');
 const url=new URL(info.url);if(url.protocol!=='https:'||url.hostname!=='upload.wikimedia.org')throw Error('Untrusted Commons media URL');
 const media=await fetch(url,{redirect:'error',signal:AbortSignal.timeout(90000)});if(!media.ok||Number(media.headers.get('content-length'))>maxBytes||!media.body)throw Error('Commons mirror unavailable or exceeds limit');
 const reader=media.body.getReader(),chunks:Uint8Array[]=[];let size=0;
 try{for(;;){const next=await reader.read();if(next.done)break;size+=next.value.length;if(size>maxBytes)throw Error('Commons media exceeds limit');chunks.push(next.value);}}finally{await reader.cancel();}
 await writeFile(dest,Buffer.concat(chunks),{mode:0o600});return {method:'public_commons_mirror',url:url.href,license,evidence};
}
export async function dailyTick(){
 await mkdir(folder,{recursive:true});
 const policy=validateDailyPolicy({...DEFAULT_DAILY_POLICY,...await read(policyPath,{})});
 const s=await read(statePath,{version:1,days:{},items:[]});
 const save=async()=>{s.updated_at=new Date().toISOString();await atomic(statePath,s);};
 if(!policy.enabled){s.status='disabled';await save();return s;}
 if(policy.wait_for_demo){const demo=await read(join(root,'data/hackathon-demo/constraints.json'),{});if(demo.recording_status!=='completed'&&demo.recording_status!=='done'&&!demo.demo_complete){s.status='waiting_for_demo';await save();return s;}}
 const day=dayKey(new Date(),policy.time_zone);s.day=day;
 const api=async(path:string,init:RequestInit={})=>{const r=await fetch(new URL(path,policy.api_base),{...init,signal:AbortSignal.timeout(120000)});if(!r.ok)throw Error(`Local analysis HTTP ${r.status}`);return r.json();};
 const store=new WorkflowStore(join(folder,'analysis-workflows.sqlite3'));
 try{
  const used=()=>store.all().filter(r=>r.month===new Date().toISOString().slice(0,7)).reduce((n,r)=>n+r.reserved,0)+taskBudgetTotal(store.db);
  // Reconcile all previous-day jobs too; never abandon an imported video at midnight.
  const pending=s.items.find((r:any)=>['analysis','crew','rendering','importing'].includes(r.stage));
  if(pending){
   if(pending.stage==='importing'){
    const games=(await api('/api/games')).items;const game=games.find((g:any)=>g.source_sha256===pending.sha256);
    if(!game){pending.stage='failed';pending.error='Ambiguous import: source not registered; no automatic re-upload';await save();return s;}
    pending.game_id=game.game_id;pending.job_id=game.analysis_job_id||(await api(`/api/games/${game.game_id}/jobs`)).items.at(-1)?.job_id;pending.stage='analysis';await save();return s;
   }
   const jobs=(await api(`/api/games/${pending.game_id}/jobs`)).items;const job=jobs.find((j:any)=>j.job_id===pending.job_id);
   if(!job){pending.stage='failed';pending.error='Imported analysis job missing';}
   else if(['queued','running','cancelling'].includes(job.status)&&Date.now()-Date.parse(pending.imported_at)>30*60*1000){await api(`/api/jobs/${pending.job_id}/cancel`,{method:'POST'});pending.stage='failed';pending.error='Analysis exceeded 30-minute bound; cancellation requested';}
   else if(['failed','cancelled'].includes(job.status)){pending.stage=job.status;pending.error='Analysis '+job.status;}
   else if(job.status==='succeeded'){
    pending.stage='crew';const flow=store.get(pending.job_id);
    if(flow?.cancel_requested){pending.stage='cancelled';}
    else if(flow?.state==='succeeded'){
     if(!flow.output?.clips?.length){pending.stage='skipped';pending.error='No eligible grounded clips';}
     else{
      pending.stage='rendering';await save();
      try{const {portraitExport}=await import('./portrait-export.js');const exported=await portraitExport(pending.job_id,pending.game_id,flow.output.clips[0],policy.api_base,{format:pending.format} as any);pending.stage='completed';pending.output_path=exported.path;pending.output_sha256=exported.sha256;pending.preview_url=`http://127.0.0.1:8011/previews/${pending.job_id}`;pending.completed_at=new Date().toISOString();}
      catch(e){pending.stage='skipped';pending.error=safeError(e);}
     }
    }else if(flow&&!['running','submitting','cancelling','baseline_skipped'].includes(flow.state)){pending.stage='skipped';pending.error=flow.error||'Crew '+flow.state;}
    else if(Date.now()-Date.parse(pending.imported_at)>60*60*1000){pending.stage='failed';pending.error='Crew did not complete within 60 minutes';}
   }
   s.status=pending.stage;await save();return s;
  }
  if(!hasDailySlot(s.items,day,policy.target_per_day)){s.status='daily_target_reached';await save();return s;}
  const scoutConfig=await read(join(folder,'daily-discovery-scout.json'),{});
  if(scoutConfig.restore_pending)throw Error('Scout default restoration pending; refusing paid discovery');
  const pinnedVersion=scoutConfig.agent_version;
  if(pinnedVersion!==undefined&&(!Number.isSafeInteger(pinnedVersion)||pinnedVersion<1))throw Error('Invalid daily Scout immutable version');
  let d=s.days[day];
  if(!d){
   if(used()+policy.scout_budget_micro_usd+1000000>9000000){s.status='budget_limited';await save();return s;}
   // Reuse a pinned, deny-by-default read-only version of the EXISTING Scout. No agent edits or new crew.
   const seed=await read(join(folder,'youtube-discovery-demo-v1.json'),null);
   if(!seed?.request?.agent_version)throw Error('Pinned read-only Scout configuration missing');
   d=s.days[day]={key:'playlattice-daily-discovery-'+day+'-round-0',round:0,previous_rounds:[],created_ms:Date.now(),request:{...seed.request,agent_version:pinnedVersion||seed.request.agent_version,agent_id:crew.ingress[0].id,budget_micro_usd:Math.floor(policy.scout_budget_micro_usd/3),metadata:{playlattice_daily_discovery:day},output_schema:{type:'object',additionalProperties:false,properties:{candidates:{type:'array',maxItems:15,items:{type:'object',additionalProperties:false,properties:{youtube_url:{type:'string'},title:{type:'string'},creator:{type:'string'},license_evidence_url:{type:'string'},license_summary:{type:'string'},camera_evidence:{type:'string'},camera_verified:{type:'boolean'},start_seconds:{type:'number'}},required:['youtube_url','title','creator','license_evidence_url','license_summary','camera_evidence','camera_verified','start_seconds']}},limitations:{type:'array',items:{type:'string'}}},required:['candidates','limitations']},message:`Discover up to15 NEW original Creative Commons basketball gameplay videos for ${day}. Need five usable videos today. Exclude previously seen IDs: ${s.items.map((r:any)=>r.source_id).join(', ')}. Prefer stationary full/half-court adult basketball. Use at most3 searches,8 page reads,4 browser inspections. Only return camera_verified true if consecutive actual video frames were inspected; cite concrete evidence and a gameplay start_seconds for a ${policy.excerpt_seconds}-second window. Unknown camera verification false. Explicit current Creative Commons reuse evidence required; no broadcaster compilations. No account actions, publishing, outreach, delegation, scheduling, or downloads. Return candidates and honest limitations using submit_output.`}};
   reserveTask(store.db,d.key,'Daily basketball source discovery',d.request.budget_micro_usd);await save();
  }
  const client=naiveClient(d.key);
  if(!d.session_id&&!d.output){const session=await client.sessions.create(d.request);d.session_id=session.id;await save();s.status='discovering';await save();return s;}
  if(!d.output){
   const session=await client.sessions.get(d.session_id);d.session_status=session.status;
   if(session.status==='idle'){
    settleTask(store.db,d.key,session.consumed_micro_usd,session.id);d.settled_cost_micro_usd=session.consumed_micro_usd;
    if(session.stop_reason!=='end_turn'||!session.structured_output){d.output={candidates:[],limitations:['Scout stopped without complete structured output']};s.status='discovery_failed';await save();return s;}
    d.output=session.structured_output;await save();
   }else if(Date.now()-d.created_ms>10*60*1000){await client.sessions.cancel(session.id);d.output={candidates:[],limitations:['Scout exceeded 10 minute wall clock bound']};s.status='discovery_timeout';await save();return s;}
   else{s.status='discovering';await save();return s;}
  }
  const attempts=s.items.filter((r:any)=>r.day===day).length;
  if(attempts>=policy.max_attempts_per_day){s.status='daily_attempt_limit';await save();return s;}
  const candidates=d.output.candidates||[];
  const candidate=candidates.find((c:any)=>youtubeId(c.youtube_url)&&!s.items.some((r:any)=>r.source_id===youtubeId(c.youtube_url)));
  if(!candidate){
   const previous=d.previous_rounds||[];const round=d.round||0;
   const spent=previous.reduce((n:number,r:any)=>n+r.cost_micro_usd,0)+(d.settled_cost_micro_usd??d.request.budget_micro_usd);
   const remaining=policy.scout_budget_micro_usd-spent;
   const budget=nextRoundBudget(policy.scout_budget_micro_usd,previous.map((r:any)=>r.cost_micro_usd),d.settled_cost_micro_usd,round);
   if(budget&&attempts<policy.max_attempts_per_day){
    const next={key:'playlattice-daily-discovery-'+day+'-round-'+(round+1),round:round+1,previous_rounds:[...previous,{key:d.key,session_id:d.session_id,cost_micro_usd:d.settled_cost_micro_usd,limitations:d.output.limitations}],created_ms:Date.now(),request:{...d.request,agent_version:pinnedVersion||d.request.agent_version,budget_micro_usd:budget,message:d.request.message+' Additional bounded round: find DIFFERENT candidates. Exclude all IDs already attempted: '+s.items.map((r:any)=>r.source_id).join(', ')}};
    reserveTask(store.db,next.key,'Daily basketball source discovery',budget);s.days[day]=next;s.status='next_discovery_round';await save();return s;
   }
   s.status=remaining<1000?'daily_discovery_budget_limited':'no_more_verified_sources';await save();return s;
  }
  const row:any={day,source_id:youtubeId(candidate.youtube_url),candidate,stage:'validating',format:s.items.filter((r:any)=>r.game_id).length%2?'story':'quiz',created_at:new Date().toISOString()};s.items.push(row);await save();
  try{
   if(!eligibleCandidate(candidate))throw Error('Scout did not verify consecutive gameplay frames and license evidence');
   if(used()+1000000>9000000){row.stage='budget_limited';s.status='budget_limited';await save();return s;}
   const dest=join(folder,'daily-discovery',row.source_id);await mkdir(dest,{recursive:true});
   const url='https://www.youtube.com/watch?v='+row.source_id;
   const meta=JSON.parse((await run('yt-dlp',['--no-playlist','--no-warnings','--skip-download','--dump-single-json',url],{timeout:45000,maxBuffer:5*1024*1024})).stdout);
   if(meta.id!==row.source_id||!/^Creative Commons Attribution/i.test(meta.license||'')||!Number.isFinite(meta.duration)||meta.duration>policy.max_source_seconds||candidate.start_seconds+policy.excerpt_seconds>meta.duration)throw Error('Current source metadata did not verify CC license/duration/window');
   row.metadata={id:meta.id,title:meta.title,creator:meta.uploader,license:meta.license,duration:meta.duration,source_url:url};await atomic(join(dest,'provenance.json'),{...row.metadata,candidate,verified_at:new Date().toISOString()});
   const source=join(dest,'source.mp4');
   try{await run('yt-dlp',['--no-playlist','--no-warnings','--no-overwrites','--retries','1','--fragment-retries','1','--max-filesize',String(policy.max_source_bytes),'-f','best[height<=720][ext=mp4]/best[height<=720]','--merge-output-format','mp4','-o',source,url],{timeout:180000,maxBuffer:1024*1024});row.retrieval={method:'public_youtube',url};}
   catch{row.retrieval=await commonsMirror(candidate.license_evidence_url,row.source_id,source,policy.max_source_bytes);}

   if((await stat(source)).size>policy.max_source_bytes)throw Error('Source too large');
   const excerpt=join(dest,'gameplay.mp4');
   await run('ffmpeg',['-hide_banner','-loglevel','error','-y','-ss',String(candidate.start_seconds),'-i',source,'-t',String(policy.excerpt_seconds),'-an','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',excerpt],{timeout:90000,maxBuffer:1024*1024});
   // Reject scene changes inside the proposed gameplay window. Do not call this a camera-accuracy metric.
   const cuts=await run('ffmpeg',['-hide_banner','-i',excerpt,'-vf',"select='gt(scene,0.35)',showinfo",'-an','-f','null','-'],{timeout:45000,maxBuffer:1024*1024});
   if(/Parsed_showinfo.*pts_time:/.test(cuts.stderr))throw Error('Proposed gameplay window contains a scene cut');
   const bytes=await readFile(excerpt);row.sha256=digest(bytes);row.source_sha256=digest(await readFile(source));row.path=excerpt;
   if(s.items.some((r:any)=>r!==row&&r.source_sha256===row.source_sha256&&r.game_id)){row.stage='skipped';row.error='Identical original source already imported under another URL';await save();return s;}
   await atomic(join(dest,'provenance.json'),{...row.metadata,candidate,retrieval:row.retrieval,source_sha256:row.source_sha256,ingested_sha256:row.sha256,verified_at:new Date().toISOString()});
   const games=(await api('/api/games')).items;const duplicate=games.find((g:any)=>g.source_sha256===row.sha256);
   if(duplicate){row.stage='skipped';row.error='Identical footage already registered';await save();return s;}
   // Persist intent before the write. Recovery uses hash lookup and never blindly repeats an upload.
   row.stage='importing';row.import_attempted=true;row.imported_at=new Date().toISOString();await save();
   const form=new FormData();form.append('video',new Blob([bytes],{type:'video/mp4'}),'Daily-'+row.source_id+'.mp4');form.append('rights_confirmed','true');form.append('auto_analyze','true');
   const game=await api('/api/games',{method:'POST',body:form});row.game_id=game.game_id;row.job_id=game.analysis_job_id||(await api(`/api/games/${game.game_id}/jobs`)).items.at(-1)?.job_id;row.stage='analysis';
  }catch(e){if(row.stage!=='importing')row.stage='failed';row.error=safeError(e);}
  s.status=row.stage;await save();return s;
 }finally{store.close();}
}
async function main(){
 await mkdir(folder,{recursive:true});
 if(process.argv.includes('--status')){console.log(JSON.stringify({policy:await read(policyPath,DEFAULT_DAILY_POLICY),state:await read(statePath,{status:'not_started'})},null,2));return;}
 const lockPath=join(folder,'daily-discovery.lock');
 try{const pid=Number(await readFile(lockPath,'utf8'));try{process.kill(pid,0);console.log(JSON.stringify({status:'already_running'}));return;}catch(e){if((e as NodeJS.ErrnoException).code!=='ESRCH')throw e;}await unlink(lockPath);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 const lock=await open(lockPath,'wx',0o600);await lock.writeFile(String(process.pid));await lock.close();
 let stop=false;for(const signal of ['SIGTERM','SIGINT'] as const)process.on(signal,()=>{stop=true;});
 try{do{try{const s=await dailyTick();console.log(JSON.stringify({event:'daily_discovery_tick',status:s.status,day:s.day,completed:s.items?.filter((r:any)=>r.stage==='completed').length}));}catch(e){console.error(JSON.stringify(safeError(e)));}if(!process.argv.includes('--watch')||stop)break;await sleep(60000);}while(!stop);}finally{await unlink(lockPath);}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)await main();

import { createHash } from 'node:crypto';
import { readFile,writeFile,rename,readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers/promises';
import { naiveClient,safeError } from './client.js';
import { WorkflowStore } from './analysis-store.js';
import { publicTikTokTestPost,selectTestWorkflow } from './publish-contract.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const identity='idn_kyebzf0eg9myzy39dmf7vfrg07';
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');
const base='http://127.0.0.1:8000';
const read=async(path:string)=>{const r=await fetch(base+path,{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`Local HTTP ${r.status}`);return r.json();};
try {
 const jobFlag=process.argv.indexOf('--job');
 const requestedJob=jobFlag>=0?process.argv[jobFlag+1]:undefined;
 if(jobFlag>=0&&!requestedJob)throw new Error('--job requires an ID');
 const workflow=selectTestWorkflow(requestedJob?[store.get(requestedJob)].filter(Boolean):store.all());
 if(!workflow)throw new Error('No completed crew workflow with clips');
 const selected=workflow.output.clips[0];
 publicTikTokTestPost('fil_preflight',selected,workflow.output.source_sha256);
 const current=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/jobs`);
 if(current.items.at(-1)?.job_id!==workflow.job_id||current.items.at(-1)?.status!=='succeeded')throw new Error('Source analysis was superseded');
 const clips=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/clips`);
 const clip=clips.items.find((c:any)=>c.clip_id===selected.clip_id);
 if(!clip||clip.job_id!==workflow.job_id||clip.qa_status!=='passed'||!clip.decoded||clip.source_sha256!==workflow.output.source_sha256||clip.source_start_ms!==selected.source_start_ms||clip.source_end_ms!==selected.source_end_ms||!/^\/api\/media\/[a-zA-Z0-9_-]+$/.test(clip.uri))throw new Error('Clip provenance no longer matches');
 const key='playlattice-tiktok-public-v1-'+clip.clip_id;
 const file=folder+'publish-public-tiktok-'+clip.clip_id+'.json';
 let state:any;
 try{state=JSON.parse(await readFile(file,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 state ||= {key,created_ms:Date.now(),job_id:workflow.job_id,clip_id:clip.clip_id,identity,visibility:'public',platform:'tiktok',source_sha256:clip.source_sha256,media_sha256:clip.sha256};
 const save=async()=>{await writeFile(file+'.tmp',JSON.stringify(state,null,2),{mode:0o600});await rename(file+'.tmp',file);};
 if(!state.file_id) {
  for(const filename of await readdir(folder)) {
   if(!/^publish-(?:test|private-tiktok|public-tiktok)-.*\.json$/.test(filename))continue;
   const prior=JSON.parse(await readFile(folder+filename,'utf8'));
   if(prior.file_id&&prior.media_sha256===clip.sha256&&prior.source_sha256===clip.source_sha256&&!prior.cleanup?.deleted){state.file_id=prior.file_id;break;}
  }
 }
 const client=naiveClient(key);
 if(!state.account_id||Date.now()-(state.account_verified_ms||state.created_ms)>10*60*1000) {
  const accounts=await client.social.accounts(identity,{limit:100});
  if(accounts.has_more)throw new Error('Account inventory incomplete');
  const target=accounts.data.filter(a=>a.platform==='tiktok');
  if(target.length!==1)throw new Error('Exactly one connected TikTok account is required');
  state.account_id=target[0].id;state.account_verified_ms=Date.now();await save();
 }
 if(!state.file_id) {
  const name=`${key}.mp4`;
  const existing=state.upload_attempted?await client.files.list({search:name,scope:'published',limit:100}):null;
  if(existing?.has_more)throw new Error('File reconciliation incomplete');
  const known=existing?.data.find(f=>f.name===name&&f.sha256===clip.sha256);
  if(known)state.file_id=known.id;
  else {
   if(state.upload_attempted)throw new Error('Previous upload status unconfirmed; no second upload submitted');
   const r=await fetch(base+clip.uri,{redirect:'error',signal:AbortSignal.timeout(20000)});
   if(!r.ok)throw new Error(`Media HTTP ${r.status}`);
   if(Number(r.headers.get('content-length'))>10*1024*1024)throw new Error('Test clip exceeds ten MiB');
   const bytes=new Uint8Array(await r.arrayBuffer());
   if(bytes.length>10*1024*1024||createHash('sha256').update(bytes).digest('hex')!==clip.sha256)throw new Error('Media hash or size mismatch');
   const form=new FormData();form.append('file',new Blob([bytes],{type:'video/mp4'}),name);form.append('scope','published');
   state.upload_attempted=true;await save();
   try{const uploaded=await naiveClient(key+'-upload').files.upload(form);if(uploaded.sha256!==clip.sha256)throw new Error('Uploaded media hash mismatch');state.file_id=uploaded.id;}
   catch(e){if((e as any).status===429){state.upload_attempted=false;await save();}throw e;}
  }
  await save();console.log(JSON.stringify({event:'media_uploaded',file_id:state.file_id}));
 }
 state.body ||= publicTikTokTestPost(state.file_id,selected,workflow.output.source_sha256);
 if('visibility' in state.body||state.body.platforms.length!==1||state.body.platforms[0]!=='tiktok')throw new Error('Stored publication policy is not the authorized public TikTok request');
 await save();
 if(!state.post_id) {
  const existing=state.post_attempted?await client.social.listPosts({identity_id:identity,limit:100,reconcile:true}):null;
  if(existing?.has_more)throw new Error('Post reconciliation incomplete');
  const known=existing?.data.find(p=>p.file_ids.includes(state.file_id)&&p.platforms.length===1&&p.platforms[0]==='tiktok');
  if(known)state.post_id=known.id;
  else {
   if(Date.now()-state.created_ms>23*60*60*1000)throw new Error('Post replay window expired; no new post submitted');
   const latest=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/jobs`);
   if(latest.items.at(-1)?.job_id!==workflow.job_id||store.get(workflow.job_id)?.state!=='succeeded')throw new Error('Workflow changed before publication');
   state.post_attempted=true;await save();
   const post=await client.social.createPost(identity,state.body);state.post_id=post.id;
  }
  await save();console.log(JSON.stringify({event:'post_submitted',post_id:state.post_id,visibility:'public'}));
 }
 const deadline=Date.now()+180000;
 while(Date.now()<deadline) {
  const live=await client.social.getPost(identity,state.post_id);
  if(live.status==='failed') {
   state.status='failed';state.provider_error=live.error||'Provider returned failed without an error reason';state.checked_at=new Date().toISOString();await save();
   const record=store.get(workflow.job_id);record.publication={platform:'tiktok',requested_visibility:'public',post_id:state.post_id,status:'failed',error:state.provider_error};store.save(record);
   throw new Error(state.provider_error);
  }
  if(live.status!=='posted'){state.status=live.status;await save();await setTimeout(5000);continue;}
  const posts=await client.social.listPosts({identity_id:identity,limit:100,reconcile:true});
  const post=posts.data.find(p=>p.id===state.post_id);
  if(post) {
   state.status=post.status;state.url=post.url;state.verified_visibility=post.visibility;state.checked_at=new Date().toISOString();await save();
   if(post.visibility!==null&&post.visibility!=='public'){await client.social.deletePost(identity,state.post_id);state.status='removed_visibility_mismatch';await save();throw new Error('Post removed because visibility did not match public');}
   if(post.status==='failed')throw new Error('Provider reports post failed; inspect private publish journal');
   if(post.status==='posted'&&post.url){state.public_access_verified=false;try{const check=await fetch('https://www.tiktok.com/oembed?url='+encodeURIComponent(post.url),{signal:AbortSignal.timeout(15000)});const data=await check.json();state.public_access_verified=check.ok&&typeof data.html==='string';}catch{}await save();console.log(JSON.stringify({event:'tiktok_posted',post_id:post.id,url:post.url,public_access_verified:state.public_access_verified}));break;}
  }
  await setTimeout(5000);
 }
 if(state.status==='posted'&&state.url){const record=store.get(workflow.job_id);record.publication={platform:'tiktok',requested_visibility:'public',public_access_verified:state.public_access_verified||false,post_id:state.post_id,url:state.url,status:state.status};store.save(record);}
 if(state.status!=='posted'||!state.url)throw new Error('Publishing still pending; rerun reconciles the original post');
}catch(e){console.error(JSON.stringify(safeError(e)));process.exitCode=1;}finally{store.close();}

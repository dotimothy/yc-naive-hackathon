import { createHash } from 'node:crypto';
import { readFile,writeFile,rename } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { setTimeout } from 'node:timers/promises';
import { naiveClient,safeError } from './client.js';
import { WorkflowStore } from './analysis-store.js';
import { unlistedTestPost,selectTestWorkflow } from './publish-contract.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const identity='idn_kyebzf0eg9myzy39dmf7vfrg07';
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');
const base='http://127.0.0.1:8000';
const read=async(path:string)=>{const r=await fetch(base+path,{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`Local HTTP ${r.status}`);return r.json();};
try {
 const workflow=selectTestWorkflow(store.all());
 if(!workflow)throw new Error('No completed crew workflow with clips');
 const selected=workflow.output.clips[0];
 unlistedTestPost('fil_preflight',selected,workflow.output.source_sha256);
 const current=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/jobs`);
 if(current.items.at(-1)?.job_id!==workflow.job_id||current.items.at(-1)?.status!=='succeeded')throw new Error('Source analysis was superseded');
 const clips=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/clips`);
 const clip=clips.items.find((c:any)=>c.clip_id===selected.clip_id);
 if(!clip||clip.job_id!==workflow.job_id||clip.qa_status!=='passed'||!clip.decoded||clip.source_sha256!==workflow.output.source_sha256||clip.source_start_ms!==selected.source_start_ms||clip.source_end_ms!==selected.source_end_ms||!/^\/api\/media\/[a-zA-Z0-9_-]+$/.test(clip.uri))throw new Error('Clip provenance no longer matches');
 const key='playlattice-unlisted-v1-'+clip.clip_id;
 const file=folder+'publish-test-'+clip.clip_id+'.json';
 let state:any;
 try{state=JSON.parse(await readFile(file,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 state ||= {key,created_ms:Date.now(),job_id:workflow.job_id,clip_id:clip.clip_id,identity,visibility:'unlisted',platform:'youtube',source_sha256:clip.source_sha256,media_sha256:clip.sha256};
 const save=async()=>{await writeFile(file+'.tmp',JSON.stringify(state,null,2),{mode:0o600});await rename(file+'.tmp',file);};
 const client=naiveClient(key);
 if(!state.account_id||Date.now()-(state.account_verified_ms||state.created_ms)>10*60*1000) {
  const accounts=await client.social.accounts(identity,{limit:100});
  if(accounts.has_more)throw new Error('Account inventory incomplete');
  const youtube=accounts.data.filter(a=>a.platform==='youtube');
  if(youtube.length!==1)throw new Error('Exactly one connected YouTube account is required');
  state.account_id=youtube[0].id;state.account_verified_ms=Date.now();await save();
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
 state.body ||= unlistedTestPost(state.file_id,selected,workflow.output.source_sha256);
 if(state.body.visibility!=='unlisted'||state.body.platforms.length!==1||state.body.platforms[0]!=='youtube')throw new Error('Stored publication policy is not YouTube unlisted');
 await save();
 if(!state.post_id) {
  const existing=state.post_attempted?await client.social.listPosts({identity_id:identity,limit:100,reconcile:true}):null;
  if(existing?.has_more)throw new Error('Post reconciliation incomplete');
  const known=existing?.data.find(p=>p.file_ids.includes(state.file_id)&&p.platforms.length===1&&p.platforms[0]==='youtube');
  if(known)state.post_id=known.id;
  else {
   if(Date.now()-state.created_ms>23*60*60*1000)throw new Error('Post replay window expired; no new post submitted');
   const latest=await read(`/api/games/${encodeURIComponent(workflow.game_id)}/jobs`);
   if(latest.items.at(-1)?.job_id!==workflow.job_id||store.get(workflow.job_id)?.state!=='succeeded')throw new Error('Workflow changed before publication');
   state.post_attempted=true;await save();
   const post=await client.social.createPost(identity,state.body);state.post_id=post.id;
  }
  await save();console.log(JSON.stringify({event:'post_submitted',post_id:state.post_id,visibility:'unlisted'}));
 }
 const deadline=Date.now()+180000;
 while(Date.now()<deadline) {
  const posts=await client.social.listPosts({identity_id:identity,limit:100,reconcile:true});
  const post=posts.data.find(p=>p.id===state.post_id||p.file_ids.includes(state.file_id)&&p.platforms.includes('youtube'));
  if(post) {
   state.status=post.status;state.url=post.url;state.verified_visibility=post.visibility;state.checked_at=new Date().toISOString();await save();
   if(post.visibility!==null&&post.visibility!=='unlisted'){await client.social.deletePost(identity,state.post_id);state.status='removed_visibility_mismatch';await save();throw new Error('Post removed because visibility did not match unlisted');}
   if(post.status==='failed')throw new Error('Provider reports post failed; inspect private publish journal');
   if(post.status==='posted'&&post.visibility==='unlisted'&&post.url){console.log(JSON.stringify({event:'unlisted_post_verified',post_id:post.id,url:post.url,visibility:post.visibility}));break;}
  }
  await setTimeout(5000);
 }
 if(state.status!=='posted'||state.verified_visibility!=='unlisted'||!state.url)throw new Error('Publishing still pending; rerun reconciles the original post');
}catch(e){console.error(JSON.stringify(safeError(e)));process.exitCode=1;}finally{store.close();}

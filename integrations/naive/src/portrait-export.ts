import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {join,extname} from 'node:path';
import {clipperQuizSelection,QUIZ_MUSIC} from './clipper-quizzes.js';
import {probeMedia,validateTikTokMedia} from './tiktok-media.js';
import {publishingSource} from './publishing-sources.js';
import {WorkflowStore} from './analysis-store.js';
export const PORTRAIT_PROFILE='player-portrait-quiz-v1';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const data=join(root,'data/naive');
const python=process.env.PLAYLATTICE_PYTHON||join(root,'.venv/bin/python');
const bridge=join(root,'integrations/naive/scripts/portrait_export.py');
const digest=(bytes:Uint8Array|string)=>createHash('sha256').update(bytes).digest('hex');
export async function portraitExport(jobId:string,gameId:string,selected:any,base='http://127.0.0.1:8000') {
 const origin=new URL(base);if(origin.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(origin.hostname))throw new Error('Portrait source API must be loopback');
 const read=async(path:string)=>{const r=await fetch(new URL(path,origin),{signal:AbortSignal.timeout(15000)});if(!r.ok)throw new Error(`Portrait evidence HTTP ${r.status}`);return r.json();};
 const [games,jobs,tracks,highlights,shots]=await Promise.all([read('/api/games'),read(`/api/games/${encodeURIComponent(gameId)}/jobs`),read(`/api/games/${encodeURIComponent(gameId)}/tracks`),read(`/api/games/${encodeURIComponent(gameId)}/highlights`),read(`/api/games/${encodeURIComponent(gameId)}/shots`)]);
 const game=games.items.find((g:any)=>g.game_id===gameId),job=jobs.items.at(-1);
 if(!game||job?.job_id!==jobId||job.status!=='succeeded'||highlights.job_id!==jobId||shots.job_id!==jobId||game.source_sha256!==job.report?.source_sha256)throw new Error('Portrait evidence spans different analyses');
 if(!/^\/api\/media\/[a-zA-Z0-9_-]+$/.test(game.source_uri))throw new Error('Unregistered source URI');
 const snapshot:any={job_id:jobId,game_id:gameId,source_sha256:game.source_sha256,selected,attribution:publishingSource(game.source_sha256)?.footer||'Source attribution in caption',tracks:tracks.items,candidates:highlights.candidates,shots:shots.items};
 const hash=digest(JSON.stringify(snapshot));
 const folder=join(data,'portrait-exports',jobId,hash.slice(0,20));await mkdir(folder,{recursive:true});
 const source=join(data,'portrait-exports','source-'+game.source_sha256+'.mp4');
 let sourceBytes:Uint8Array|undefined;
 try{const bytes=await readFile(source);if(digest(bytes)===game.source_sha256)sourceBytes=bytes;}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 if(!sourceBytes){const response=await fetch(new URL(game.source_uri,origin),{redirect:'error',signal:AbortSignal.timeout(20000)});if(!response.ok||Number(response.headers.get('content-length'))>250*1024*1024)throw new Error('Portrait source is unavailable or too large');const bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>250*1024*1024||digest(bytes)!==game.source_sha256)throw new Error('Portrait source hash mismatch');await writeFile(source,bytes,{mode:0o600});}
 snapshot.source_path=source;await writeFile(join(folder,'snapshot.json'),JSON.stringify(snapshot),{mode:0o600});
 const run=async(args:string[],outputFolder=folder)=>promisify(execFile)(python,[bridge,'--snapshot',join(folder,'snapshot.json'),'--output-dir',outputFolder,...args],{cwd:root,timeout:180000,maxBuffer:2*1024*1024});
 await run(['--plan-only']);const plan=JSON.parse(await readFile(join(folder,'plan.json'),'utf8'));
 const quizPath=join(folder,'quizzes.json');const quizReceipt=await clipperQuizSelection(jobId,plan,quizPath);
 let musicPath:string|undefined,musicHash:string|undefined;
 if(process.env.PLAYLATTICE_QUIZ_MUSIC){const bytes=await readFile(process.env.PLAYLATTICE_QUIZ_MUSIC);musicHash=digest(bytes);musicPath=join(folder,'quiz-music-'+musicHash.slice(0,16)+(extname(process.env.PLAYLATTICE_QUIZ_MUSIC)||'.audio'));await writeFile(musicPath,bytes,{mode:0o600});}
 let musicTitle='Supplied quiz music',musicOrigin='user_supplied';
 if(!musicPath&&quizReceipt.music_id){
  const chosen=QUIZ_MUSIC.find(m=>m.id===quizReceipt.music_id);if(!chosen)throw new Error('Unrecognized clipper music choice');
  const generated=join(folder,'original-'+chosen.id+'.wav');
  await promisify(execFile)(python,[join(root,'integrations/naive/scripts/quiz_music.py'),'--music-id',chosen.id,'--output',generated],{cwd:root,timeout:15000,maxBuffer:1024*1024});
  musicPath=generated;musicHash=digest(await readFile(generated));musicTitle=chosen.title;musicOrigin='original_procedural_composition';
 }
 const identity=digest(JSON.stringify({snapshot:hash,quiz_ids:quizReceipt.selected_candidate_ids,music:musicHash||null,profile:PORTRAIT_PROFILE,renderer:digest(await readFile(join(root,'workers/render/portrait.py'))),cover_renderer:digest(await readFile(join(root,'workers/render/cover.py')))}));
 const renderFolder=join(folder,'render-'+identity.slice(0,16));await mkdir(renderFolder,{recursive:true});
 let receipt:any;
 try{const cached=JSON.parse(await readFile(join(renderFolder,'receipt.json'),'utf8'));if(cached.render_identity===identity&&digest(await readFile(cached.output_path))===cached.sha256&&cached.cover?.path&&digest(await readFile(cached.cover.path))===cached.cover.sha256)receipt=cached;}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 if(!receipt){await run(['--quizzes',quizPath,...(musicPath?['--quiz-music',musicPath]:[])],renderFolder);receipt=JSON.parse(await readFile(join(renderFolder,'receipt.json'),'utf8'));receipt.render_identity=identity;receipt.clipper_quiz_agent=quizReceipt;receipt.quiz_music_title=musicTitle;receipt.music_origin=musicOrigin;await writeFile(join(renderFolder,'receipt.json'),JSON.stringify(receipt,null,2),{mode:0o600});}
 const metadata=await probeMedia(receipt.output_path);const checked=validateTikTokMedia(metadata);
 const bytes=await readFile(receipt.output_path);
 if(checked.width*16!==checked.height*9||checked.fps!==30||!receipt.captions_burned_in||!receipt.decoded||digest(bytes)!==receipt.sha256)throw new Error('Portrait presentation failed technical QA');
 const store=new WorkflowStore(join(data,'analysis-workflows.sqlite3'));
 try{const r=store.get(jobId);if(!r||r.state!=='succeeded')throw new Error('Parent workflow no longer succeeded');r.presentation={profile:PORTRAIT_PROFILE,path:receipt.output_path,receipt_path:join(renderFolder,'receipt.json'),music_path:musicPath||null,cover_path:receipt.cover?.path||null,preview_uri:`http://127.0.0.1:8011/previews/${encodeURIComponent(jobId)}`,track_count:receipt.track_count,quiz_count:receipt.quiz_count,quiz_music_missing:receipt.quiz_music_missing};store.save(r);}finally{store.close();}
 return {path:receipt.output_path,bytes,sha256:receipt.sha256,metadata,recipe:receipt};
}

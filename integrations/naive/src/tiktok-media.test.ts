import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFile,spawnSync} from 'node:child_process';
import {promisify} from 'node:util';
import {frameRate,validateTikTokMedia,prepareTikTokMedia,type MediaProbe} from './tiktok-media.js';
const valid=():MediaProbe=>({streams:[{codec_type:'video',codec_name:'h264',pix_fmt:'yuv420p',width:1280,height:720,r_frame_rate:'30/1',avg_frame_rate:'30/1'}],format:{duration:'7.257',format_name:'mov,mp4,m4a,3gp,3g2,mj2'}});
test('real failing 999/50 FPS is rejected; supported frame rates pass',()=>{
 const p=valid();p.streams[0].r_frame_rate=p.streams[0].avg_frame_rate='999/50';assert.equal(frameRate('999/50'),19.98);assert.throws(()=>validateTikTokMedia(p),/23–60/);
 for(const rate of ['23/1','24/1','30000/1001','30/1','60/1']){const x=valid();x.streams[0].r_frame_rate=x.streams[0].avg_frame_rate=rate;assert.doesNotThrow(()=>validateTikTokMedia(x));}
});
test('publishing preflight rejects invalid dimensions, missing video and unsupported encoding',()=>{
 assert.throws(()=>validateTikTokMedia({...valid(),streams:[]}));
 for(const patch of [{width:359},{height:4097},{codec_name:'mpeg4'},{pix_fmt:'yuv444p'},{avg_frame_rate:'0/0'},{r_frame_rate:'120/1'}]){const p=valid();Object.assign(p.streams[0],patch);assert.throws(()=>validateTikTokMedia(p));}
});
const available=spawnSync('ffmpeg',['-version'],{stdio:'ignore'}).status===0&&spawnSync('ffprobe',['-version'],{stdio:'ignore'}).status===0;
test('actual 19.98 FPS video becomes a decoded 30 FPS derivative without changing source bytes or timing',{skip:!available},async()=>{
 const folder=await mkdtemp(join(tmpdir(),'playlattice-tiktok-'));
 try{
  const source=join(folder,'source.mp4');await promisify(execFile)('ffmpeg',['-nostdin','-v','error','-f','lavfi','-i','color=c=black:s=480x360:r=999/50','-t','3.25','-c:v','libx264','-pix_fmt','yuv420p',source]);
  const bytes=await readFile(source),hash=createHash('sha256').update(bytes).digest('hex');
  const result=await prepareTikTokMedia(bytes,hash,folder);assert.equal(validateTikTokMedia(result.metadata).fps,30);assert.equal(result.recipe.decoded,true);assert.ok(Math.abs(result.recipe.duration_delta_ms)<=120);
  assert.equal(createHash('sha256').update(await readFile(source)).digest('hex'),hash);
  const cached=await prepareTikTokMedia(bytes,hash,folder);assert.equal(cached.sha256,result.sha256);
  await assert.rejects(prepareTikTokMedia(bytes,'wrong-hash',folder),/hash/);
 }finally{await rm(folder,{recursive:true,force:true});}
});

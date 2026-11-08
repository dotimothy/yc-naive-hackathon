import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash,randomUUID} from 'node:crypto';
import {mkdir,readFile,writeFile,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
const exec=promisify(execFile);
export const TIKTOK_PROFILE='tiktok-h264-30fps-v1';
export type MediaProbe={streams:Array<{codec_type?:string;codec_name?:string;pix_fmt?:string;width?:number;height?:number;r_frame_rate?:string;avg_frame_rate?:string}>;format:{format_name?:string;duration?:string;size?:string}};
export function frameRate(value:string|undefined) {const [a,b=1]=(value||'').split('/').map(Number);return b>0?a/b:NaN;}
export function validateTikTokMedia(p:MediaProbe) {
 const video=p.streams.find(s=>s.codec_type==='video');
 if(!video)throw new Error('TikTok export has no video');
 const fps=frameRate(video.avg_frame_rate), nominal=frameRate(video.r_frame_rate);
 if(!Number.isFinite(fps)||!Number.isFinite(nominal)||fps<23||fps>60||nominal<23||nominal>60)throw new Error(`TikTok requires 23–60 FPS; got ${fps}`);
 if(video.codec_name!=='h264'||video.pix_fmt!=='yuv420p')throw new Error('Publishing profile requires H.264 yuv420p');
 if(!Number.isInteger(video.width)||!Number.isInteger(video.height)||video.width!<360||video.width!>4096||video.height!<360||video.height!>4096)throw new Error('TikTok requires dimensions between 360 and 4096 pixels');
 const seconds=Number(p.format.duration);if(!Number.isFinite(seconds)||seconds<=0||seconds>600)throw new Error('Unsupported TikTok export duration');
 if(!p.format.format_name?.split(',').includes('mp4'))throw new Error('Publishing profile requires MP4');
 if(p.streams.some(s=>s.codec_type==='audio'&&s.codec_name!=='aac'))throw new Error('Publishing profile requires AAC audio');
 return {fps,width:video.width!,height:video.height!,duration_ms:Math.round(seconds*1000)};
}
export async function probeMedia(path:string):Promise<MediaProbe> {
 const {stdout}=await exec('ffprobe',['-v','error','-show_entries','format=format_name,duration,size:stream=codec_type,codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate','-of','json',path],{timeout:20000,maxBuffer:1024*1024});return JSON.parse(stdout);
}
/** Publishing-only derivative: duplicate/drop frames on the existing timeline; never rerun inference. */
export async function prepareTikTokMedia(bytes:Uint8Array,expectedHash:string,folder:string) {
 const inputHash=createHash('sha256').update(bytes).digest('hex');
 if(inputHash!==expectedHash)throw new Error('Registered clip hash does not match media bytes');
 await mkdir(folder,{recursive:true});
 const stem=join(folder,inputHash+'-'+TIKTOK_PROFILE),output=stem+'.mp4',receipt=stem+'.json';
 try {
  const saved=JSON.parse(await readFile(receipt,'utf8'));
  const cached=await readFile(output);
  if(saved.input_sha256===inputHash&&saved.profile===TIKTOK_PROFILE&&saved.sha256===createHash('sha256').update(cached).digest('hex')) {
   const metadata=await probeMedia(output);validateTikTokMedia(metadata);return {path:output,bytes:cached,sha256:saved.sha256,metadata,recipe:saved};
  }
 }catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 const id=randomUUID(),input=stem+'-'+id+'-input.mp4',temporary=stem+'-'+id+'.mp4';
 await writeFile(input,bytes,{mode:0o600});
 try {
  const original=await probeMedia(input);
  await exec('ffmpeg',['-nostdin','-v','error','-y','-i',input,'-map','0:v:0','-map','0:a:0?','-sn','-dn','-vf','fps=30','-fps_mode','cfr','-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','2','-filter_threads','1','-c:a','aac','-ar','48000','-b:a','128k','-movflags','+faststart',temporary],{timeout:60000,maxBuffer:1024*1024});
  const metadata=await probeMedia(temporary);const checked=validateTikTokMedia(metadata);
  const before=original.streams.find(s=>s.codec_type==='video');
  if(before?.width!==checked.width||before?.height!==checked.height||Math.abs(Number(original.format.duration)*1000-checked.duration_ms)>120)throw new Error('Publishing conversion changed source dimensions or duration');
  await exec('ffmpeg',['-nostdin','-v','error','-threads','2','-i',temporary,'-f','null','-'],{timeout:60000,maxBuffer:1024*1024});
  const encoded=await readFile(temporary),sha256=createHash('sha256').update(encoded).digest('hex');
  const recipe={profile:TIKTOK_PROFILE,input_sha256:inputHash,sha256,input_metadata:original,output_metadata:metadata,decoded:true,duration_delta_ms:checked.duration_ms-Number(original.format.duration)*1000};
  await rename(temporary,output);await writeFile(receipt,JSON.stringify(recipe,null,2),{mode:0o600});
  return {path:output,bytes:encoded,sha256,metadata,recipe};
 }finally {await unlink(input).catch(()=>{});await unlink(temporary).catch(()=>{});}
}

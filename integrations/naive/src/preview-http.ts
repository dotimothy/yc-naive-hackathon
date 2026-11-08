import type {IncomingMessage,ServerResponse} from 'node:http';
import {readFile,realpath,stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {resolve,sep,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import type {WorkflowStore} from './analysis-store.js';
export function byteRange(header:string|undefined,size:number) {
 if(!header)return {start:0,end:size-1,partial:false};
 const match=header.match(/^bytes=(\d*)-(\d*)$/);if(!match||!size||(!match[1]&&!match[2]))throw new Error('Invalid range');
 let start=match[1]?Number(match[1]):Math.max(0,size-Number(match[2]));
 const end=match[1]&&match[2]?Math.min(size-1,Number(match[2])):size-1;
 if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||start>=size||end<start)throw new Error('Range unavailable');
 return {start,end,partial:true};
}
export async function previewRequest(req:IncomingMessage,res:ServerResponse,store:WorkflowStore,baseFolder:string) {
 const path=new URL(req.url||'/','http://127.0.0.1').pathname;
 const match=path.match(/^\/previews\/([a-zA-Z0-9_-]+)(?:\/(video|manifest|music))?\/?$/);
 if(!match||!['GET','HEAD'].includes(req.method||''))return false;
 const record=store.get(match[1]),presentation=record?.presentation;
 if(!presentation){res.statusCode=404;res.end('Preview unavailable');return true;}
 res.setHeader('Cache-Control','no-store');
 if(!match[2]){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(await readFile(fileURLToPath(new URL('./portrait-preview.html',import.meta.url))));return true;}
 const allowed=await realpath(baseFolder);
 const contained=async(path:string)=>{const target=await realpath(path);if(!target.startsWith(allowed+sep))throw new Error('Unregistered preview artifact');return target;};
 if(match[2]==='manifest'){
  const receipt=JSON.parse(await readFile(await contained(presentation.receipt_path),'utf8'));
  const {output_path,...publicReceipt}=receipt;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(publicReceipt));return true;
 }
 if(match[2]==='music'&&!presentation.music_path){res.statusCode=404;res.end('Quiz music has not been supplied');return true;}
 const file=await contained(match[2]==='music'?presentation.music_path:presentation.path);
 const info=await stat(file);let range;
 try{range=byteRange(req.headers.range,info.size);}catch{res.statusCode=416;res.setHeader('Content-Range',`bytes */${info.size}`);res.end();return true;}
 res.statusCode=range.partial?206:200;res.setHeader('Accept-Ranges','bytes');res.setHeader('Content-Length',range.end-range.start+1);
 res.setHeader('Content-Type',match[2]==='video'?'video/mp4':({'.mp3':'audio/mpeg','.wav':'audio/wav','.m4a':'audio/mp4','.aac':'audio/aac'}[extname(file)]||'application/octet-stream'));
 if(range.partial)res.setHeader('Content-Range',`bytes ${range.start}-${range.end}/${info.size}`);
 if(req.method==='HEAD')res.end();else createReadStream(file,{start:range.start,end:range.end}).on('error',()=>res.destroy()).pipe(res);
 return true;
}

import type {IncomingMessage,ServerResponse} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
export async function discoveryRequest(req:IncomingMessage,res:ServerResponse,folder:string){
 const url=new URL(req.url||'/','http://127.0.0.1');
 if(req.method!=='GET'||!['/discovery/youtube-demo','/discovery/youtube-demo/manifest'].includes(url.pathname))return false;
 res.setHeader('Cache-Control','no-store');
 if(url.pathname.endsWith('/manifest')){
  try{const m=JSON.parse(await readFile(folder+'youtube-discovery/manifest.json','utf8'));const keys=['source_url','creator','title','license','license_url','license_evidence_url','source_start_ms','requested_source_end_ms','source_sha256','ingested_sha256','game_id','job_id','transform','camera_evidence','retrieval_method','publishing','analysis_status','source_uri','clips','scout_session_id','scout_report_session_id','scout_cost_micro_usd','scout_tool_counts','portrait_status','portrait_reason'];const safe=Object.fromEntries(keys.filter(k=>k in m).map(k=>[k,m[k]]));if(Array.isArray(safe.clips))safe.clips=safe.clips.map((c:any)=>Object.fromEntries(['clip_id','uri','source_start_ms','source_end_ms','qa_status','decoded'].filter(k=>k in c).map(k=>[k,c[k]])));res.setHeader('Content-Type','application/json');res.end(JSON.stringify(safe));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;res.statusCode=404;res.end('{}');}
 }else{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(await readFile(fileURLToPath(new URL('./discovery-demo.html',import.meta.url))));}return true;
}

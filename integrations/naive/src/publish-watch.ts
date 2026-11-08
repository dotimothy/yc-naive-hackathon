import {readFile,mkdir,open,unlink} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {setTimeout} from 'node:timers/promises';
import {WorkflowStore} from './analysis-store.js';
import {publicationTick,type PublishingPolicy} from './publication-queue.js';
import {safeError} from './client.js';
import {PORTRAIT_PROFILE} from './portrait-export.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const root=fileURLToPath(new URL('../',import.meta.url));
await mkdir(folder,{recursive:true});
const lockPath=folder+'publish-watch.lock';
try{const pid=Number(await readFile(lockPath,'utf8'));try{process.kill(pid,0);throw new Error('Autonomous publisher already running');}catch(e){if((e as NodeJS.ErrnoException).code!=='ESRCH')throw e;}await unlink(lockPath);}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
const lock=await open(lockPath,'wx',0o600);await lock.writeFile(String(process.pid));await lock.close();
const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');let stop=false;
for(const signal of ['SIGINT','SIGTERM'] as const)process.on(signal,()=>{stop=true;});
const run=async(id:string)=>{
 try{const result=await promisify(execFile)(join(root,'node_modules/.bin/tsx'),[join(root,'src/publish-test.ts'),'--job',id],{cwd:root,timeout:600000,maxBuffer:1024*1024});process.stdout.write(result.stdout);}
 catch(e){const error=e as Error&{stdout?:string;stderr?:string};if(error.stdout)process.stdout.write(error.stdout);const row=store.get(id),clip=row?.output?.clips?.[0];
   if(clip){try{const saved=JSON.parse(await readFile(folder+'publish-public-tiktok-'+PORTRAIT_PROFILE+'-'+clip.clip_id+'.json','utf8'));if(saved.post_id&&!['failed','removed_visibility_mismatch'].includes(saved.status)){row.publication={...row.publication,status:row.publication?.cancel_requested?'cancellation_pending':'pending',post_id:saved.post_id};store.save(row);return;}}catch{}}
   throw new Error('Automatic publication did not complete; see publishing journal');}
};
console.log(JSON.stringify({event:'autonomous_publisher_ready',platform:'tiktok',requested_visibility:'public'}));
try{while(!stop){try{const policy=JSON.parse(await readFile(folder+'publishing-policy.json','utf8')) as PublishingPolicy;await publicationTick(store,policy,run);}catch(e){console.error(JSON.stringify(safeError(e)));}await setTimeout(5000);}}
finally{store.close();await unlink(lockPath);}

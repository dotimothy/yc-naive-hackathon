import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {setTimeout} from 'node:timers/promises';
import {naiveClient} from './client.js';
import {crew} from './crew.js';
import {WorkflowStore} from './analysis-store.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
export const QUIZ_MUSIC=[{id:'suspense_pulse',title:'Suspense Pulse',description:'Original minor-key pulse with soft ticking rhythm for prediction questions'},{id:'soft_countdown',title:'Soft Countdown',description:'Original gentle major-key plucked countdown, light and playful'},{id:'quiet_chords',title:'Quiet Chords',description:'Original low-energy chord bed behind analysis'}] as const;
const schema={type:'object',additionalProperties:false,properties:{music_id:{type:'string',enum:QUIZ_MUSIC.map(m=>m.id)},selected_candidate_ids:{type:'array',maxItems:3,uniqueItems:true,items:{type:'string'}}},required:['selected_candidate_ids','music_id']};
export function validateQuizSelection(value:unknown,candidates:any[]) {
 const result=value as {selected_candidate_ids?:unknown};const ids=result?.selected_candidate_ids;
 if(!Array.isArray(ids)||ids.length>3||new Set(ids).size!==ids.length||ids.some(id=>typeof id!=='string'||!candidates.some(c=>c.candidate_id===id)))throw new Error('Clipper selected an unknown or duplicated quiz');
 const chosen=ids.map(id=>candidates.find(c=>c.candidate_id===id));
 if(new Set(chosen.map(q=>q.track_id)).size!==chosen.length)throw new Error('Clipper selected duplicate quizzes for one track');
 return ids as string[];
}
async function version() {
 const c=naiveClient(),path=folder+'quiz-clipper-version-v2.json';let state:any;
 try{state=JSON.parse(await readFile(path,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 const save=()=>writeFile(path,JSON.stringify(state,null,2),{mode:0o600});
 if(state?.restore_pending) {
  const current=await c.agents.get(crew.ingress[1].id);
  const original=JSON.parse(await readFile(folder+'quiz-clipper-original-v2.json','utf8'));
  if(current.system===original.system){state.restore_pending=false;await save();}
  else if(current.metadata.playlattice_quizzes==='v2') {state.version=current.current_version;await c.agents.rollback(current.id,{to_version:state.original_version,expected_version:current.current_version});state.restore_pending=false;await save();}
  else throw new Error('Concurrent clipper update prevents quiz-version restoration');
 }
 if(state?.version&&!state.restore_pending)return state.version as number;
 const original=await c.agents.get(crew.ingress[1].id);
 await writeFile(folder+'quiz-clipper-original-v2.json',JSON.stringify(original,null,2),{mode:0o600});
 state={original_version:original.current_version,restore_pending:true};await save();
 const updated=await c.agents.update(original.id,{expected_version:original.current_version,
  system:'You are PlayLattice’s existing clipper agent, responsible for prediction quizzes in portrait player breakdowns. Choose an appropriate original quiz-music cue and up to three clear quiz moments from supplied analysis candidates, at most one per track. The question, options and correct answer are already grounded in later observations in the same camera shot. Select only candidate IDs and a music_id from the supplied original cue catalog; never invent another event, identity, shot outcome, answer or copyrighted song. You also own the choice of countdown music. Prefer clearly measured motion and multiple highlighted tracks. Empty selection is valid when evidence is insufficient. This is metadata only, not visual inspection. Work autonomously without questions or approvals. Another local renderer places the pause, choices, reveal and music cues; the local publisher alone posts. Do not post or call external tools.',
  tools:{default_config:{permission:'deny'},configs:{}},skills:[],mcp_servers:[],multiagent:false,handoffs:false,output_schema:schema,structured_output_required:true,
  metadata:{...original.metadata,playlattice_quizzes:'v2'},
 });state.version=updated.current_version;await save();
 await c.agents.rollback(original.id,{to_version:original.current_version,expected_version:updated.current_version});state.restore_pending=false;await save();return updated.current_version;
}
export async function clipperQuizSelection(jobId:string,plan:any,outputPath:string) {
 await mkdir(folder,{recursive:true});
 const candidates=plan.quiz_candidates||[];
 if(!candidates.length){const output={selected_candidate_ids:[],agent_id:crew.ingress[1].id,reason:'No grounded future observation can support a quiz'};await writeFile(outputPath,JSON.stringify(output,null,2),{mode:0o600});return output;}
 const facts={job_id:jobId,source_sha256:plan.source_sha256,candidates,music_catalog:QUIZ_MUSIC};
 const hash=createHash('sha256').update(JSON.stringify(facts)).digest('hex');
 const path=folder+'clipper-quiz-task-'+hash+'.json';let state:any;
 try{state=JSON.parse(await readFile(path,'utf8'));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 const store=new WorkflowStore(folder+'analysis-workflows.sqlite3');
 try {
  if(state?.output){validateQuizSelection(state.output,candidates);if(!QUIZ_MUSIC.some(m=>m.id===state.output.music_id))throw new Error('Invalid cached music choice');await writeFile(outputPath,JSON.stringify(state.output,null,2),{mode:0o600});return state.output;}
  const agentVersion=state?.agent_version||await version();
  state ||= {created_ms:Date.now(),key:'playlattice-clipper-quiz-v2-'+hash,agent_version:agentVersion};
  const save=()=>writeFile(path,JSON.stringify(state,null,2),{mode:0o600});
  const reservation='quiz-v2-'+hash;store.reservePublication(jobId,reservation,150000);
  state.body ||= {agent_id:crew.ingress[1].id,agent_version:agentVersion,budget_micro_usd:150000,output_schema:schema,structured_output_required:true,
   message:'Select evidence-grounded prediction quizzes and choose the original quiz-music cue that best fits this player-breakdown video. Return selected_candidate_ids and music_id via submit_output. Candidates: '+JSON.stringify(facts)};
  await save();const c=naiveClient();
  if(!state.session_id){if(Date.now()-state.created_ms>23*60*60*1000)throw new Error('Quiz submission replay window expired');const s=await c.sessions.create(state.body,{idempotencyKey:state.key});state.session_id=s.id;await save();}
  const deadline=Date.now()+240000;
  while(Date.now()<deadline) {
   const s=await c.sessions.get(state.session_id);
   if(s.status==='idle'&&s.stop_reason==='end_turn') {
    const ids=validateQuizSelection(s.structured_output,candidates);
    const musicId=s.structured_output?.music_id;if(!QUIZ_MUSIC.some(m=>m.id===musicId))throw new Error('Clipper selected an unknown music cue');
    state.output={selected_candidate_ids:ids,music_id:musicId,agent_id:crew.ingress[1].id,agent_version:agentVersion,session_id:s.id,cost_micro_usd:s.consumed_micro_usd,grounding:'Later tracking observations from the same shot; no guessed basketball outcomes'};
    store.settleReservation(jobId,reservation,s.consumed_micro_usd);await save();await writeFile(outputPath,JSON.stringify(state.output,null,2),{mode:0o600});return state.output;
   }
   if(!['running','queued'].includes(s.status))throw new Error(`Clipper quiz task ended ${s.status}/${s.stop_reason}`);
   await setTimeout(2000);
  }
  await c.sessions.cancel(state.session_id);throw new Error('Clipper quiz task timed out; cancellation requested');
 }finally{store.close();}
}

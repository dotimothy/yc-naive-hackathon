import {crew,crewMembers} from './crew.js';

/* Live crew activity for the dashboard, built only from records the Naive sessions API exposes:
   session/thread state, and the per-session event log (tool calls, send_to_agent handoffs,
   thread reports, completed messages, model-request spans, retries). Reasoning text is never
   exposed by the API and is not shown. Fetching is incremental (after_seq) and bounded. */

export type SessionLike={id:string;agent_id:string;status:string;stop_reason:string|null;last_seq:number;started_at:string|null;ended_at:string|null;created_at:string;consumed_micro_usd:number;model?:string|null;pending_actions?:ReadonlyArray<{kind:string;name:string}>;structured_output?:unknown};
export type EventLike={seq:number;type:string;data:Record<string,unknown>;created_at:string};
export type SessionsApi={
 get(id:string):Promise<SessionLike>;
 threads(id:string,query?:{limit?:number;after?:string}):Promise<{data:SessionLike[];has_more:boolean;next_cursor:string|null}>;
 events(id:string,query?:{after_seq?:number;limit?:number}):Promise<{data:EventLike[];has_more:boolean}>;
};
export type Entry={id:string;at:string;kind:'handoff'|'report'|'tool'|'tool_result'|'message'|'state'|'model'|'error';summary:string;detail?:string;truncated?:boolean;from?:string;to?:string};
export type AgentState='working'|'waiting_agent'|'waiting_provider'|'waiting_input'|'queued'|'paused'|'done'|'idle'|'stopped'|'failed'|'cancelled'|'no_session'|'unknown';

const ACTIVE=['submitting','running','cancelling'];
const DETAIL_MAX=400,SUMMARY_MAX=160;
const roster=[crew.coordinator,...crewMembers];
const nameOf=(agentId:string|undefined)=>roster.find(a=>a.id===agentId)?.name;

/** Strip credentials and local filesystem paths; the feed is served to the browser. */
export function redact(text:string){
 return text.replace(/sk_(?:live|test)_[\w.-]+/g,'[redacted key]')
  .replace(/(?:file:\/\/)?\/(?:Users|home|private|var\/folders|tmp|Volumes)\/[^\s"'`,)\]}]*/g,'[local path]');
}
function clip(v:unknown,max=DETAIL_MAX){
 const raw=typeof v==='string'?v:v===undefined||v===null?'':JSON.stringify(v);
 const text=redact(raw).replace(/\s+/g,' ').trim();
 return text.length>max?{text:text.slice(0,max-1)+'…',truncated:true}:{text,truncated:false};
}
const str=(v:unknown)=>typeof v==='string'?v:undefined;

type Track={session:SessionLike|null;afterSeq:number;historyTruncated:boolean;behind:boolean;entries:Entry[];
 openTools:Map<string,{name:string;at:string;to?:string}>;modelOpenAt:string|null;retryingAt:string|null;lastEventAt:string|null;
 toolCalls:number;toolErrors:number;firstTask:string|null;lastReply:string|null;structured:boolean};
const newTrack=():Track=>({session:null,afterSeq:0,historyTruncated:false,behind:false,entries:[],openTools:new Map(),modelOpenAt:null,retryingAt:null,lastEventAt:null,toolCalls:0,toolErrors:0,firstTask:null,lastReply:null,structured:false});

/** Map one API event onto the track; returns the timeline entry it produced, if any. */
export function applyEvent(t:Track,ev:EventLike,sessionId:string,agent:string,maxEntries=40):Entry|null{
 const d=ev.data||{};const id=`${sessionId}:${ev.seq}`;const at=ev.created_at;t.lastEventAt=at;
 let e:Entry|null=null;
 const mk=(kind:Entry['kind'],summary:string,detail?:unknown,extra:Partial<Entry>={})=>{const s=clip(summary,SUMMARY_MAX).text;const c=detail===undefined?null:clip(detail);return {id,at,kind,summary:s,...(c&&c.text?{detail:c.text,...(c.truncated?{truncated:true}:{})}:{}),...extra} as Entry;};
 switch(ev.type){
  case 'tool.started':{
   const name=str(d.name)||'tool';const args=(d.args&&typeof d.args==='object'?d.args:{}) as Record<string,unknown>;t.toolCalls++;
   if(name==='send_to_agent'){const to=str(args.agent)||'agent';t.openTools.set(str(d.tool_call_id)||id,{name,at,to});e=mk('handoff',`Sent task to ${to}${args.wait?' and waits for the reply':''}`,args.message,{from:agent,to});}
   else{t.openTools.set(str(d.tool_call_id)||id,{name,at});e=mk('tool',name==='submit_output'?'Submitting structured output':`Called ${name}`,Object.keys(args).length?args:undefined);}
   break;}
  case 'tool.completed':{
   const name=str(d.name)||'tool';t.openTools.delete(str(d.tool_call_id)||'');
   if(d.is_error){t.toolErrors++;e=mk('error',`${name} returned an error`,d.output);}
   else if(name==='submit_output'){t.structured=true;e=mk('tool_result','Structured output submitted');}
   else e=mk('tool_result',name==='send_to_agent'?'Hand-off accepted':`${name} finished`,d.output);
   break;}
  case 'tool.confirm':e=mk('state',`Waiting for approval to run ${str(d.name)||'a tool'}`);break;
  case 'tool.confirm.resolved':case 'tool.confirm.answered':e=mk('state','Approval answered');break;
  case 'thread.created':e=mk('state',`Opened a thread for ${str(d.member)||nameOf(str(d.agent_id))||'a member'}`,undefined,{from:agent,to:str(d.member)});break;
  case 'thread.idle':{const m=str(d.member)||'member';const done=d.stop_reason==='end_turn';
   e=mk(done?'report':'state',done?`${m} reported back`:`${m} went idle (${str(d.stop_reason)||'no reason'})`,d.summary,{from:m,to:agent});break;}
  case 'thread.terminated':e=mk('state',`${str(d.member)||'Member'} thread ended`,undefined,{from:str(d.member),to:agent});break;
  case 'message.completed':{
   const role=str(d.role);const actor=d.actor&&typeof d.actor==='object'?str((d.actor as any).type):undefined;
   if(role==='user'){const c=clip(d.content);t.firstTask??=c.text||null;e=mk('message',actor==='key'?'Task received from PlayLattice integration':'Task message received',d.content,actor==='key'?{from:'PlayLattice',to:agent}:{to:agent});}
   else if(role==='assistant'){const c=clip(d.content);if(c.text){t.lastReply=c.text;e=mk('message','Wrote a reply',d.content,{from:agent});}}
   break;}
  case 'session.running':t.retryingAt=null;e=mk('state','Session running');break;
  case 'session.idle':{t.modelOpenAt=null;t.retryingAt=null;const r=str(d.stop_reason);
   if(d.structured_output)t.structured=true;
   e=mk('state',r==='end_turn'?'Finished its turn':r==='awaiting_delegation'?'Paused while delegated agents work':`Idle (${r?r.replace(/_/g,' '):'no reason'})`);break;}
  case 'session.retrying':t.retryingAt=at;e=mk('error','Provider request is being retried',Object.keys(d).length?d:undefined);break;
  case 'session.degraded':e=mk('error','Session degraded',Object.keys(d).length?d:undefined);break;
  case 'budget.exceeded':e=mk('error','Session budget exceeded');break;
  case 'span.model_request.start':t.modelOpenAt=at;break;
  case 'span.model_request.end':{t.modelOpenAt=null;t.retryingAt=null;const ms=Number(d.provider_ms);
   e=mk('model',`Model responded${Number.isFinite(ms)?` in ${(ms/1000).toFixed(1)}s`:''}${str(d.stop_reason)?` (${d.stop_reason})`:''}`);break;}
  case 'outcome.evaluation.completed':e=mk('state',`Outcome evaluation: ${str(d.result)||'completed'}`,d.explanation);break;
  case 'media.job.queued':case 'media.job.completed':case 'file.imported':e=mk('tool',ev.type.replace(/\./g,' '));break;
  case 'media.job.failed':e=mk('error','Media job failed',d.error);break;
 }
 if(e){t.entries.push(e);if(t.entries.length>maxEntries)t.entries.splice(0,t.entries.length-maxEntries);}
 return e;
}

/** Current state from session fields plus what the event tail proves; "unknown" when it proves nothing. */
export function deriveState(s:SessionLike|null,t:Track|undefined,runActive:boolean,running:string[]):{state:AgentState;label:string;waiting_on:string[]}{
 if(!s)return runActive?{state:'no_session',label:'No session started yet',waiting_on:[]}:{state:'no_session',label:'No session in this run',waiting_on:[]};
 const pending=(s.pending_actions||[]).map(p=>p.name);
 const open=t?[...t.openTools.values()].at(-1):undefined;
 switch(s.status){
  case 'queued':return {state:'queued',label:'Queued by Naive',waiting_on:[]};
  case 'running':
   if(t?.retryingAt)return {state:'waiting_provider',label:'Waiting on model provider (retrying)',waiting_on:['model provider']};
   if(open?.name==='send_to_agent'&&open.to)return {state:'waiting_agent',label:`Waiting on ${open.to}`,waiting_on:[open.to]};
   if(open)return {state:'working',label:`Running tool ${open.name}`,waiting_on:[]};
   if(t?.modelOpenAt)return {state:'working',label:'Model request in progress',waiting_on:[]};
   return {state:'working',label:'Running',waiting_on:[]};
  case 'idle':
   switch(s.stop_reason){
    case 'awaiting_delegation':return running.length?{state:'waiting_agent',label:`Waiting on ${running.join(', ')}`,waiting_on:running}:{state:'waiting_agent',label:'Waiting on delegated agents',waiting_on:[]};
    case 'awaiting_input':case 'awaiting_answer':case 'awaiting_approval':return {state:'waiting_input',label:`Waiting for ${s.stop_reason.replace('awaiting_','')}${pending.length?` (${pending.join(', ')})`:''}`,waiting_on:['operator']};
    case 'budget_paused':return {state:'paused',label:'Paused: session budget reached',waiting_on:[]};
    case 'end_turn':return {state:'done',label:'Finished',waiting_on:[]};
    case null:return {state:'idle',label:'Idle',waiting_on:[]};
    default:return {state:'stopped',label:`Stopped: ${s.stop_reason.replace(/_/g,' ')}`,waiting_on:[]};
   }
  case 'completed':return {state:'done',label:'Completed',waiting_on:[]};
  case 'failed':return {state:'failed',label:`Failed${s.stop_reason?`: ${s.stop_reason.replace(/_/g,' ')}`:''}`,waiting_on:[]};
  case 'cancelled':return {state:'cancelled',label:'Cancelled',waiting_on:[]};
  default:return {state:'unknown',label:`Unknown state (${s.status})`,waiting_on:[]};
 }
}
const settled=(s:SessionLike|null)=>!!s&&(['completed','failed','cancelled'].includes(s.status)||s.status==='idle'&&s.stop_reason==='end_turn');

type RunRef={job_id:string;game_id?:string;state:string;session_id?:string;created_ms?:number;completed_at?:string;members?:{session_id?:string;agent_id?:string}[]};

export class TeamActivity {
 private tracks=new Map<string,Track>();
 private runThreads=new Map<string,Map<string,string>>(); // job -> agent_id -> thread session id
 private frozen=new Set<string>();
 private inflight:Promise<void>|null=null;
 private last=-Infinity;
 fetchedAt:string|null=null;error:string|null=null;
 calls=0;
 constructor(private api:SessionsApi,private opts:{minIntervalMs?:number;pageLimit?:number;maxPages?:number;initialWindow?:number;maxEntries?:number;maxActiveRuns?:number;now?:()=>number}={}){}
 private get o(){return {minIntervalMs:5000,pageLimit:100,maxPages:3,initialWindow:250,maxEntries:40,maxActiveRuns:3,now:Date.now,...this.opts};}

 /** Active runs (newest first, bounded) plus the most recent finished run for context. */
 pick(records:RunRef[]){
  const active=records.filter(r=>ACTIVE.includes(r.state)).slice(0,this.o.maxActiveRuns);
  const finished=records.find(r=>r.session_id&&!ACTIVE.includes(r.state));
  return [...active.map(r=>({r,current:true})),...(finished?[{r:finished,current:false}]:[])];
 }

 /** Single-flight, throttled refresh. Callers may race it against a short timeout. */
 refresh(records:RunRef[]):Promise<void>{
  if(this.inflight)return this.inflight;
  if(this.o.now()-this.last<this.o.minIntervalMs)return Promise.resolve();
  this.inflight=(async()=>{
   try{
    const runs=this.pick(records);const keep=new Set<string>();
    for(const {r,current} of runs){await this.refreshRun(r,current);
     keep.add(r.session_id||'');for(const id of this.runThreads.get(r.job_id)?.values()||[])keep.add(id);}
    for(const id of [...this.tracks.keys()])if(!keep.has(id))this.tracks.delete(id);
    const jobs=new Set(runs.map(x=>x.r.job_id));
    for(const j of [...this.runThreads.keys()])if(!jobs.has(j)){this.runThreads.delete(j);this.frozen.delete(j);}
    this.fetchedAt=new Date(this.o.now()).toISOString();this.error=null;
   }catch(e){
    const err=e as {status?:number;code?:string;message?:string};
    this.error=err.status?`Naive API ${err.status}${err.code?` ${err.code}`:''}`:redact(err.message||'Activity refresh failed').slice(0,200);
   }finally{this.last=this.o.now();this.inflight=null;}
  })();
  return this.inflight;
 }

 private track(id:string){let t=this.tracks.get(id);if(!t){t=newTrack();this.tracks.set(id,t);}return t;}
 private async events(id:string,t:Track,agent:string){
  const lastSeq=t.session?.last_seq??0;
  if(t.afterSeq===0&&lastSeq>this.o.initialWindow){t.afterSeq=lastSeq-this.o.initialWindow;t.historyTruncated=true;}
  t.behind=false;
  for(let page=0;t.afterSeq<lastSeq;page++){
   if(page>=this.o.maxPages){t.behind=true;return;}
   this.calls++;const res=await this.api.events(id,{after_seq:t.afterSeq,limit:this.o.pageLimit});
   for(const ev of res.data){if(ev.seq<=t.afterSeq)continue;applyEvent(t,ev,id,agent,this.o.maxEntries);t.afterSeq=ev.seq;}
   if(!res.has_more||!res.data.length)return;
  }
 }
 private async refreshRun(r:RunRef,current:boolean){
  if(!r.session_id||this.frozen.has(r.job_id))return;
  this.calls++;const root=await this.api.get(r.session_id);const rt=this.track(root.id);rt.session=root;
  await this.events(root.id,rt,crew.coordinator.name);
  const threads:SessionLike[]=[];let after:string|undefined;
  for(let page=0;page<3;page++){this.calls++;const res=await this.api.threads(root.id,{limit:100,...(after?{after}:{})});threads.push(...res.data);if(!res.has_more||!res.next_cursor||res.next_cursor===after)break;after=res.next_cursor;}
  const map=new Map<string,string>();
  for(const th of threads){if(!nameOf(th.agent_id))continue;const prev=map.get(th.agent_id);
   // Keep the newest thread per agent; a coordinator can re-delegate.
   if(prev&&(this.tracks.get(prev)?.session?.created_at||'')>th.created_at)continue;map.set(th.agent_id,th.id);}
  this.runThreads.set(r.job_id,map);
  for(const th of threads){if(map.get(th.agent_id)!==th.id)continue;const t=this.track(th.id);t.session=th;await this.events(th.id,t,nameOf(th.agent_id)!);}
  const all=[rt,...[...map.values()].map(id=>this.tracks.get(id)!)];
  if(!current&&all.every(t=>settled(t.session)&&!t.behind&&t.afterSeq>=(t.session?.last_seq??0)))this.frozen.add(r.job_id);
 }

 snapshot(records:RunRef[],cancelledJobs:ReadonlySet<string>=new Set()){
  const runs=this.pick(records).map(({r,current})=>{
   const live=current;
   const cancellationRequested=current&&cancelledJobs.has(r.job_id);
   const root=r.session_id?this.tracks.get(r.session_id):undefined;
   const threads=this.runThreads.get(r.job_id)||new Map<string,string>();
   const runningMembers=crewMembers.filter(a=>{const s=this.tracks.get(threads.get(a.id)||'')?.session;return s&&(s.status==='running'||s.status==='queued');}).map(a=>a.name);
   const agents=roster.map(a=>{
    const isCeo=a.id===crew.coordinator.id;
    const t=isCeo?root:this.tracks.get(threads.get(a.id)||'');const s=t?.session??null;
    const state=deriveState(s,t,live,isCeo?runningMembers:[]);
    // Root-session records that name this member: hand-offs to it and its reports back.
    const fromRoot=isCeo?[]:(root?.entries||[]).filter(e=>e.to===a.name||e.from===a.name);
    const timeline=[...(t?.entries||[]),...fromRoot].sort((x,y)=>x.at<y.at?-1:x.at>y.at?1:x.id<y.id?-1:1).slice(-this.o.maxEntries);
    const handoff=[...fromRoot].reverse().find(e=>e.kind==='handoff');
    const report=[...fromRoot].reverse().find(e=>e.kind==='report');
    const errors=timeline.filter(e=>e.kind==='error').slice(-3);
    const lastAction=[...timeline].reverse().find(e=>e.kind!=='model')||timeline.at(-1)||null;
    return {name:a.name,agent_id:a.id,session_id:s?.id??null,status:s?.status??null,stop_reason:s?.stop_reason??null,model:s?.model??null,
     started_at:s?.started_at??null,ended_at:s?.ended_at??null,last_event_at:t?.lastEventAt??null,
     state:state.state,state_label:state.label,waiting_on:state.waiting_on,
     task:isCeo?(t?.firstTask??null):(handoff?.detail??t?.firstTask??null),
     latest_action:lastAction,tool_calls:t?.toolCalls??0,tool_errors:t?.toolErrors??0,
     open_tool:t?[...t.openTools.values()].at(-1)?.name??null:null,
     output:isCeo?(t?.structured?'Structured package submitted':t?.lastReply??null):(report?.detail??t?.lastReply??null),
     errors,cost_micro_usd:s?.consumed_micro_usd??null,pending_actions:(s?.pending_actions||[]).map(p=>p.name),
     history_truncated:t?.historyTruncated??false,catching_up:t?.behind??false,timeline};
   });
   return {job_id:r.job_id,game_id:r.game_id??null,workflow_state:r.state,current:live,cancellation_requested:cancellationRequested,root_session_id:r.session_id??null,
    created_ms:r.created_ms??null,completed_at:r.completed_at??null,agents};
  });
  return {fetched_at:this.fetchedAt,error:this.error,runs,
   limits:{entries_per_agent:this.o.maxEntries,page_limit:this.o.pageLimit,max_pages_per_session:this.o.maxPages,initial_window:this.o.initialWindow,min_refresh_ms:this.o.minIntervalMs}};
 }
}

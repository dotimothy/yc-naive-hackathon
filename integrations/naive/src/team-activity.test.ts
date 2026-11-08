import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TeamActivity,applyEvent,deriveState,redact,type SessionLike,type EventLike,type SessionsApi} from './team-activity.js';
import {crew} from './crew.js';

const scout=crew.ingress[0],clipper=crew.ingress[1];
const sess=(id:string,agent:string,extra:Partial<SessionLike>={}):SessionLike=>({id,agent_id:agent,status:'running',stop_reason:null,last_seq:0,started_at:'2026-11-08T07:00:00.000Z',ended_at:null,created_at:'2026-11-08T07:00:00.000Z',consumed_micro_usd:0,...extra});
const ev=(seq:number,type:string,data:Record<string,unknown>={}):EventLike=>({seq,type,data,created_at:new Date(Date.UTC(2026,10,8,7,0,seq)).toISOString()});
const track=()=>({session:null,afterSeq:0,historyTruncated:false,behind:false,entries:[],openTools:new Map(),modelOpenAt:null,retryingAt:null,lastEventAt:null,toolCalls:0,toolErrors:0,firstTask:null,lastReply:null,structured:false});

test('maps real hand-offs, reports and tool errors; drops deltas and spans; redacts paths and keys',()=>{
 const t=track();
 assert.equal(applyEvent(t,ev(1,'message.delta',{text:'x'}),'s','CEO'),null);
 assert.equal(applyEvent(t,ev(2,'span.harness_load.start'),'s','CEO'),null);
 const h=applyEvent(t,ev(3,'tool.started',{name:'send_to_agent',tool_call_id:'c1',args:{agent:'scout',wait:true,message:'check /Users/tim/secret/video.mp4 with sk_live_abc123'}}),'s','CEO')!;
 assert.equal(h.kind,'handoff');assert.equal(h.from,'CEO');assert.equal(h.to,'scout');
 assert.ok(!h.detail!.includes('/Users/')&&!h.detail!.includes('sk_live'));
 const r=applyEvent(t,ev(4,'thread.idle',{member:'scout',summary:'done',stop_reason:'end_turn'}),'s','CEO')!;
 assert.equal(r.kind,'report');assert.equal(r.from,'scout');assert.equal(r.to,'CEO');
 const e=applyEvent(t,ev(5,'tool.completed',{name:'web',tool_call_id:'c2',is_error:true,output:'boom'}),'s','CEO')!;
 assert.equal(e.kind,'error');assert.equal(t.toolErrors,1);
 const long=applyEvent(t,ev(6,'message.completed',{role:'assistant',content:'y'.repeat(2000)}),'s','CEO')!;
 assert.ok(long.truncated&&long.detail!.length<=400);
 assert.equal(redact('see file:///private/var/x.json'),'see [local path]');
});

test('derives waiting only from evidence and reports unknown otherwise',()=>{
 assert.equal(deriveState(sess('r',crew.coordinator.id,{status:'idle',stop_reason:'awaiting_delegation'}),undefined,true,['clipper']).label,'Waiting on clipper');
 assert.equal(deriveState(sess('r',crew.coordinator.id,{status:'idle',stop_reason:'awaiting_delegation'}),undefined,true,[]).label,'Waiting on delegated agents');
 const t=track();applyEvent(t,ev(1,'session.retrying',{}),'s','scout');
 assert.equal(deriveState(sess('s',scout.id),t,true,[]).state,'waiting_provider');
 const t2=track();applyEvent(t2,ev(1,'tool.started',{name:'send_to_agent',tool_call_id:'a',args:{agent:'clipper',wait:true}}),'r','CEO');
 assert.equal(deriveState(sess('r',crew.coordinator.id),t2,true,[]).state,'waiting_agent');
 assert.equal(deriveState(sess('s',scout.id,{status:'idle',stop_reason:'end_turn'}),undefined,true,[]).state,'done');
 assert.equal(deriveState(sess('s',scout.id,{status:'mystery'}),undefined,true,[]).state,'unknown');
 assert.equal(deriveState(null,undefined,true,[]).state,'no_session');
});

function fakeApi(sessions:Map<string,SessionLike>,events:Map<string,EventLike[]>,threads:string[]){
 const calls:{m:string;id:string;after?:number}[]=[];let fail=false;
 const api:SessionsApi={
  async get(id){calls.push({m:'get',id});if(fail)throw Object.assign(new Error('x'),{status:503,code:'unavailable'});return {...sessions.get(id)!};},
  async threads(id){calls.push({m:'threads',id});return {data:threads.map(t=>({...sessions.get(t)!})),has_more:false,next_cursor:null};},
  async events(id,q){calls.push({m:'events',id,after:q?.after_seq});const all=(events.get(id)||[]).filter(e=>e.seq>(q?.after_seq??0));const page=all.slice(0,q?.limit??100);return {data:page,has_more:all.length>page.length};},
 };
 return {api,calls,setFail:(v:boolean)=>{fail=v;}};
}

test('fetches incrementally with bounded pages and freezes finished runs',async()=>{
 const rootEvents=Array.from({length:30},(_,i)=>ev(i+1,i===0?'tool.started':'session.usage',i===0?{name:'send_to_agent',tool_call_id:'c',args:{agent:'scout',message:'look'}}:{}));
 const sessions=new Map([['root',sess('root',crew.coordinator.id,{status:'idle',stop_reason:'awaiting_delegation',last_seq:30})],['th',sess('th',scout.id,{last_seq:2})]]);
 const events=new Map([['root',rootEvents],['th',[ev(1,'message.completed',{role:'user',content:'look'}),ev(2,'span.model_request.start')]]]);
 const {api,calls,setFail}=fakeApi(sessions,events,['th']);
 let now=0;const a=new TeamActivity(api,{minIntervalMs:5000,pageLimit:10,maxPages:2,initialWindow:150,now:()=>now});
 const runs=[{job_id:'j',game_id:'g',state:'running',session_id:'root'}];
 await a.refresh(runs);
 assert.equal(calls.filter(c=>c.m==='events'&&c.id==='root').length,2,'stops at maxPages');
 let snap=a.snapshot(runs);const ceo=snap.runs[0].agents[0],sc=snap.runs[0].agents[1];
 assert.equal(ceo.catching_up,true);assert.equal(ceo.state,'waiting_agent');assert.deepEqual(ceo.waiting_on,['scout']);
 assert.equal(sc.task,'look');assert.equal(sc.state_label,'Model request in progress');
 assert.equal(snap.runs[0].agents.find(x=>x.agent_id===clipper.id)!.state,'no_session');
 calls.length=0;await a.refresh(runs);assert.equal(calls.length,0,'throttled');
 now=6000;await a.refresh(runs);
 assert.deepEqual(calls.filter(c=>c.m==='events').map(c=>c.after),[20,30].slice(0,1),'resumes after last seq');
 // Snapshot survives an API outage.
 now=12000;setFail(true);await a.refresh(runs);snap=a.snapshot(runs);
 assert.match(snap.error!,/503/);assert.equal(snap.runs[0].agents[1].task,'look');
 // A finished run is fetched once, then never again.
 setFail(false);sessions.set('root',sess('root',crew.coordinator.id,{status:'idle',stop_reason:'end_turn',last_seq:30}));sessions.set('th',sess('th',scout.id,{status:'idle',stop_reason:'end_turn',last_seq:2}));
 const done=[{job_id:'j',game_id:'g',state:'succeeded',session_id:'root'}];
 now=18000;await a.refresh(done);now=24000;calls.length=0;await a.refresh(done);
 assert.equal(calls.length,0,'frozen');snap=a.snapshot(done);assert.equal(snap.runs[0].current,false);
});

test('long sessions start from a bounded tail window, and workflows without sessions report no data',async()=>{
 const sessions=new Map([['root',sess('root',crew.coordinator.id,{last_seq:1000})]]);
 const {api,calls}=fakeApi(sessions,new Map([['root',[ev(990,'session.running')]]]),[]);
 const a=new TeamActivity(api,{initialWindow:50,now:()=>0});
 await a.refresh([{job_id:'j',state:'running',session_id:'root'}]);
 assert.equal(calls.find(c=>c.m==='events')!.after,950);
 assert.equal(a.snapshot([{job_id:'j',state:'running',session_id:'root'}]).runs[0].agents[0].history_truncated,true);
 const empty=new TeamActivity(api,{now:()=>0}).snapshot([{job_id:'k',state:'submitting'}]);
 assert.equal(empty.fetched_at,null);assert.ok(empty.runs[0].agents.every(x=>x.state==='no_session'));
 assert.equal(new TeamActivity(api).snapshot([]).runs.length,0);
});

test('batch cancellation labels only its own active runs and does not hide unrelated live work',()=>{
 const api={} as SessionsApi;
 const activity=new TeamActivity(api);
 const runs=[{job_id:'other',state:'running'},{job_id:'batch-job',state:'running'}];
 const snap=activity.snapshot(runs,new Set(['batch-job']));
 assert.equal(snap.runs.find(r=>r.job_id==='other')?.current,true);
 assert.equal(snap.runs.find(r=>r.job_id==='other')?.cancellation_requested,false);
 assert.equal(snap.runs.find(r=>r.job_id==='batch-job')?.current,true);
 assert.equal(snap.runs.find(r=>r.job_id==='batch-job')?.cancellation_requested,true);
});

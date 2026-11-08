/** Pure policy/state rules shared by the unattended runner and its boundary tests. */
export type DailyPolicy={enabled:boolean;target_per_day:number;time_zone:string;max_attempts_per_day:number;scout_budget_micro_usd:number;excerpt_seconds:number;max_source_seconds:number;max_source_bytes:number;api_base:string;wait_for_demo:boolean};
export const DEFAULT_DAILY_POLICY:DailyPolicy={enabled:false,target_per_day:5,time_zone:'America/Los_Angeles',max_attempts_per_day:15,scout_budget_micro_usd:150000,excerpt_seconds:30,max_source_seconds:900,max_source_bytes:150*1024*1024,api_base:'http://127.0.0.1:8000',wait_for_demo:true};
export function validateDailyPolicy(p:DailyPolicy){
 if(typeof p.enabled!=='boolean'||typeof p.wait_for_demo!=='boolean')throw Error('Policy toggles must be booleans');
 if(![p.excerpt_seconds,p.max_source_seconds,p.max_source_bytes].every(Number.isFinite))throw Error('Media bounds must be finite');
 if(!Number.isInteger(p.target_per_day)||p.target_per_day<1||p.target_per_day>5)throw Error('Daily target must be 1–5');
 if(!Number.isInteger(p.max_attempts_per_day)||p.max_attempts_per_day<p.target_per_day||p.max_attempts_per_day>15)throw Error('Daily candidate attempts must be between target and15');
 if(!Number.isInteger(p.scout_budget_micro_usd)||p.scout_budget_micro_usd<1000||p.scout_budget_micro_usd>150000)throw Error('Scout cap must be $0.001–$0.15');
 if(p.excerpt_seconds<5||p.excerpt_seconds>60||p.max_source_seconds>900||p.max_source_seconds<p.excerpt_seconds||p.max_source_bytes<1024||p.max_source_bytes>150*1024*1024)throw Error('Invalid media bounds');
 new Intl.DateTimeFormat('en-US',{timeZone:p.time_zone}).format();
 const u=new URL(p.api_base);if(u.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(u.hostname))throw Error('Daily analysis API must be loopback HTTP');
 return p;
}
export function dayKey(now:Date,timeZone:string){return new Intl.DateTimeFormat('en-CA',{timeZone,year:'numeric',month:'2-digit',day:'2-digit'}).format(now);}
export function youtubeId(raw:string){try{const u=new URL(raw);if(u.protocol!=='https:'||u.username||u.password||u.port)return null;if(u.hostname==='youtu.be')return /^[\w-]{11}$/.test(u.pathname.slice(1))?u.pathname.slice(1):null;if(!['youtube.com','www.youtube.com'].includes(u.hostname)||u.pathname!=='/watch')return null;const id=u.searchParams.get('v');return id&&/^[\w-]{11}$/.test(id)?id:null;}catch{return null;}}
export function eligibleCandidate(c:any){return !!youtubeId(c?.youtube_url)&&c.camera_verified===true&&typeof c.camera_evidence==='string'&&c.camera_evidence.length>20&&typeof c.license_evidence_url==='string'&&/^https:\/\//.test(c.license_evidence_url)&&Number.isFinite(c.start_seconds)&&c.start_seconds>=0;}
export function hasDailySlot(rows:any[],day:string,target:number){return rows.filter(r=>r.day===day&&(r.game_id||r.import_attempted)).length<target;}

/** No extra round may spend an unsettled reservation or exceed the daily cap. */
export function nextRoundBudget(cap:number,priorCosts:number[],settled:number|undefined,round:number){
 if(settled===undefined||round>=2||!Number.isSafeInteger(settled)||settled<0)return 0;
 const remaining=cap-priorCosts.reduce((a,b)=>a+b,0)-settled;
 return remaining>=1000?Math.min(Math.floor(cap/3),remaining):0;
}

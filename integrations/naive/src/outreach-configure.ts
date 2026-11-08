import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {naiveClient,safeError} from './client.js';
import {crew} from './crew.js';
import {outreachSchema,outreachSystem} from './outreach-contract.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
try{
 await mkdir(folder,{recursive:true});const c=naiveClient('playlattice-outreach-config-v1');
 let state:any;try{state=JSON.parse(await readFile(folder+'outreach-agent.json','utf8'));}catch(e){if((e as NodeJS.ErrnoException).code!=='ENOENT')throw e;}
 let agent=state?.agent_id?await c.agents.get(state.agent_id):null;
 if(!agent){const agents=[];let after:string|undefined;for(let n=0;n<10;n++){const r=await c.agents.list({limit:100,...(after?{after}:{})});agents.push(...r.data);if(!r.has_more)break;if(!r.next_cursor||n===9)throw Error('Agent inventory incomplete');after=r.next_cursor;}
  const existing=agents.filter(a=>a.name==='outreach');if(existing.length>1)throw Error('Ambiguous outreach agents');
  if(existing.length){agent=await c.agents.get(existing[0].id);if(agent.metadata.playlattice_role!=='outreach-v1')throw Error('Existing outreach agent requires review before reuse');}
  else agent=await c.agents.create({name:'outreach',model:'z-ai/glm-5.3-flash',window:'immediate',
   budget:{cap_micro_usd:1000000,max_task_micro_usd:50000,period:'month'},system:outreachSystem,
   tools:{default_config:{permission:'deny'},configs:{}},skills:[],mcp_servers:[],multiagent:false,handoffs:false,
   output_schema:outreachSchema,structured_output_required:true,metadata:{playlattice_role:'outreach-v1'}});
 }
 state={agent_id:agent.id,name:'outreach',version:agent.current_version,model:agent.model};
 await writeFile(folder+'outreach-agent.json',JSON.stringify(state,null,2),{mode:0o600});
 const ceo=await c.agents.get(crew.coordinator.id);const team=ceo.multiagent;
 if(!team||typeof team!=='object'||team.type!=='coordinator')throw Error('CEO is not a coordinator');
 if(!team.agents.some(a=>a.type==='agent'&&a.id===agent.id)){
  await writeFile(folder+'outreach-ceo-before.json',JSON.stringify(ceo,null,2),{mode:0o600});
  const updated=await naiveClient('playlattice-outreach-ceo-v1-'+ceo.current_version).agents.update(ceo.id,{expected_version:ceo.current_version,
   system:ceo.system+'\nTeam addition: outreach handles inbound AgentMail customer conversations, catalog recommendations and clarification of requested source clips. Use outreach for customer email tasks. Outreach is not a required participant in the existing five-specialist video-analysis workflow. It does not send unsolicited messages or publish posts; local code validates edits and performs email delivery.',
   multiagent:{...team,agents:[...team.agents,{type:'agent',id:agent.id}]}});
  state.ceo_version=updated.current_version;
 }
 state.verified_at=new Date().toISOString();await writeFile(folder+'outreach-agent.json',JSON.stringify(state,null,2),{mode:0o600});
 console.log(JSON.stringify({...state,status:'configured',agentmail_credentials_shared_with_agent:false}));
}catch(e){console.error(JSON.stringify(safeError(e)));process.exitCode=1;}

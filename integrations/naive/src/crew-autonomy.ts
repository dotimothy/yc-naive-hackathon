import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {naiveClient,safeError} from './client.js';
import {crew,crewMembers,validateCrew} from './crew.js';
const folder=fileURLToPath(new URL('../../../data/naive/',import.meta.url));
const marker='[PlayLattice autonomous publishing instruction v1]';
const instruction=marker+' The operator authorizes unattended processing and public TikTok test publication to the connected PlayLattice account. This supersedes older requirements to ask for clip approval or to publish to YouTube. Do not ask a human to select, approve, caption or publish clips. Complete your assigned role automatically. Only the channel-manager owns the publication handoff; other roles must not independently post the same clip. In the local-analysis integration, the deterministic PlayLattice publisher commits the channel-manager package automatically after source, technical, budget and duplicate checks. Do not bypass that publisher or create duplicate independent posts. Use authorized sources only, preserve source/license credit, and do not invent identities, plays or official stats from timing metadata. TikTok exports must satisfy platform media requirements, including 23–60 FPS; PlayLattice prepares 30 FPS H.264 exports. If evidence is inadequate, return empty output with reasons; if a required dependency is unavailable, report a machine-readable blocked state rather than asking for approval. Respect existing caps and cancellation. No unrelated payments, account changes, messages or other-network publication are authorized.';
try {
 await mkdir(folder,{recursive:true});const client=naiveClient();
 const inventory=await client.agents.list({limit:100});if(inventory.has_more)throw new Error('Incomplete crew inventory');validateCrew(inventory.data);
 const versions:Record<string,number>={};
 for(const member of [...crewMembers,crew.coordinator]) {
  const current=await client.agents.get(member.id);
  const coordinator=member.id===crew.coordinator.id;
  const rosterMatches=typeof current.multiagent==='object'&&current.multiagent?.type==='coordinator'&&crewMembers.every(a=>current.multiagent&&typeof current.multiagent==='object'&&current.multiagent.agents.some(entry=>entry.type==='agent'&&entry.id===a.id&&'version' in entry&&entry.version===versions[a.id]));
  if(current.system.includes(marker)&&(!coordinator||rosterMatches)){versions[member.id]=current.current_version;console.log(JSON.stringify({name:member.name,status:'already_instructed',version:current.current_version}));continue;}
  await writeFile(folder+'autonomy-original-'+member.id+'.json',JSON.stringify(current,null,2),{mode:0o600});
  const configs={...current.tools.configs};
  delete configs.questions;
  for(const tool of ['ask_operator','request_tools'])configs[tool]={enabled:false,permission:'deny'};
  // The existing channel-manager publishes through the approved local handoff, not competing posts.
  for(const tool of ['social.post','post_to_channel'])configs[tool]={enabled:false,permission:'deny'};
  const updated=await client.agents.update(member.id,{expected_version:current.current_version,system:current.system.includes(marker)?current.system:current.system+'\n\n'+instruction,
   tools:{...current.tools,configs},metadata:{...current.metadata,playlattice_autonomy:'v1'},
   ...(coordinator?{multiagent:{type:'coordinator',board:true,agents:crewMembers.map(a=>({type:'agent',id:a.id,version:versions[a.id]}))}}:{}),
  });
  versions[member.id]=updated.current_version;console.log(JSON.stringify({name:member.name,status:'instructed',version:updated.current_version}));
 }
 await writeFile(folder+'crew-autonomy.json',JSON.stringify({updated_at:new Date().toISOString(),versions,instruction},null,2),{mode:0o600});
}catch(e){console.error(JSON.stringify({...safeError(e),...safeError(new Error((e as Error).message))}));process.exitCode=1;}

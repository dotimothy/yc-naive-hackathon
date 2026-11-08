/** Attribution is bound to both the imported game and exact source bytes. */
export function dailySourceAttribution(state:unknown,gameId:string,sourceSha256:string){
 const rows=(state as {items?:unknown[]})?.items;if(!Array.isArray(rows))return null;
 const clean=(value:unknown,max:number)=>typeof value==='string'?value.replace(/[\u0000-\u001f\u007f]/g,' ').replace(/\s+/g,' ').trim().slice(0,max):'';
 const row=rows.find((r:any)=>r?.game_id===gameId&&r?.sha256===sourceSha256) as any;
 if(!row?.metadata)return null;
 const title=clean(row.metadata.title,200),creator=clean(row.metadata.creator,100),license=clean(row.metadata.license,120),source_url=clean(row.metadata.source_url,500),license_evidence_url=clean(row.candidate?.license_evidence_url,500);
 if(!title||!creator||!license||!source_url)return null;
 try{if(new URL(source_url).protocol!=='https:')return null;if(license_evidence_url&&new URL(license_evidence_url).protocol!=='https:')return null;}catch{return null;}
 return {footer:`${creator.slice(0,48)} · ${license.includes('Creative Commons')?'CC BY (source linked)':license.slice(0,48)}`,title,creator,license,source_url,license_evidence_url,source_sha256:sourceSha256};
}

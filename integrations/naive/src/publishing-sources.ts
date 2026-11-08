import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {indoorSourceHash} from './publish-contract.js';
export type PublishingSource={sha256:string;name:string;source_url:string;credit:string;footer:string;authorization:string;stationary:boolean};
const path=fileURLToPath(new URL('../../../data/naive/publishing-sources.json',import.meta.url));
export function publishingSource(hash:string):PublishingSource|null {
 if(hash===indoorSourceHash)return {sha256:hash,name:'TrackID3x3',source_url:'https://github.com/open-starlab/TrackID3x3',credit:'TrackID3x3, Kazuhiro Yamada et al. CC BY 4.0: https://creativecommons.org/licenses/by/4.0/',footer:'TrackID3x3, CC BY 4.0',authorization:'Dataset CC BY 4.0',stationary:true};
 let rows:PublishingSource[];
 try{rows=JSON.parse(readFileSync(path,'utf8')).items;}catch(e){if((e as NodeJS.ErrnoException).code==='ENOENT')return null;throw e;}
 const row=rows.find(r=>r.sha256===hash);
 if(!row||!row.stationary||!row.authorization||!row.credit||!/^https:\/\//.test(row.source_url))return null;
 return row;
}

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {discoveryRequest} from './discovery-http.js';
test('discovery receipt exposes only public evidence and registered clip metadata',async()=>{
 const dir=await mkdtemp(tmpdir()+'/pl-discovery-');let body='';const response={end:(s:string)=>{body=s;},setHeader:()=>{}} as unknown as ServerResponse;
 try{await mkdir(dir+'/youtube-discovery');await writeFile(dir+'/youtube-discovery/manifest.json',JSON.stringify({source_url:'https://www.youtube.com/watch?v=7CQ9mAf_B5Y',game_id:'g',private_path:'/private/source',clips:[{clip_id:'c',uri:'/api/media/c',path:'/private/clip'}]}));
 assert.equal(await discoveryRequest({method:'GET',url:'/discovery/youtube-demo/manifest'} as IncomingMessage,response,dir+'/'),true);
 assert.ok(!body.includes('/private/'));assert.equal(JSON.parse(body).clips[0].uri,'/api/media/c');
 assert.equal(await discoveryRequest({method:'GET',url:'/discovery/other/manifest'} as IncomingMessage,response,dir+'/'),false);
 }finally{await rm(dir,{recursive:true,force:true});}
});

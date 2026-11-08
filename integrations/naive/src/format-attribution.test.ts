import {test} from 'node:test';
import assert from 'node:assert/strict';
import {dailySourceAttribution} from './format-attribution.js';
const row={game_id:'g',sha256:'hash',metadata:{title:'Basketball',creator:'Creator\nName',license:'Creative Commons Attribution',source_url:'https://www.youtube.com/watch?v=example'},candidate:{license_evidence_url:'https://commons.wikimedia.org/wiki/File:example'}};
test('daily captions retain exact source provenance without publication enrollment',()=>{
 const a=dailySourceAttribution({items:[row]},'g','hash')!;
 assert.equal(a.creator,'Creator Name');assert.equal(a.source_url,row.metadata.source_url);assert.match(a.footer,/CC BY/);
 assert.equal(dailySourceAttribution({items:[row]},'other','hash'),null);
 assert.equal(dailySourceAttribution({items:[row]},'g','different-hash'),null);
 assert.equal(dailySourceAttribution({items:[{...row,metadata:{...row.metadata,source_url:'javascript:alert(1)'}}]},'g','hash'),null);
});

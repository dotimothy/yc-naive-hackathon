import type { SocialPostCreate } from '@usenaive-sdk/managed-agents';
import { metadataCaption, type ClipEvidence } from './analysis-contract.js';
export const indoorSourceHash='8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad';
/** The operator authorized unlisted publication; never inherit platform defaults. */
export function unlistedTestPost(fileId:string, clip:ClipEvidence, sourceHash:string): SocialPostCreate {
 if(!fileId.startsWith('fil_'))throw new Error('A registered Naive file is required');
 if(sourceHash!==indoorSourceHash)throw new Error('This test publisher only supports the attributed indoor sample');
 return {platforms:['youtube'],visibility:'unlisted',file_ids:[fileId],title:'PlayLattice basketball analysis test',
  content:metadataCaption(clip)+'\n\nSource: TrackID3x3, Kazuhiro Yamada et al.\nhttps://github.com/open-starlab/TrackID3x3\nLicense: CC BY 4.0 — https://creativecommons.org/licenses/by/4.0/\nExcerpt clipped and processed by PlayLattice. No official player statistics or visual event claims.'};
}

/** Performance jobs may complete concurrently; never substitute their sources for this test. */
export function selectTestWorkflow(workflows:any[]) {
 return workflows.find(r=>r.state==='succeeded'&&r.output?.source_sha256===indoorSourceHash&&Array.isArray(r.output.clips)&&r.output.clips.length>0);
}

/** Private capability-test payload. A rejection must never silently remove privacy. */
export function privateTikTokTestPost(fileId:string,clip:ClipEvidence,sourceHash:string):SocialPostCreate {
 const attributed=unlistedTestPost(fileId,clip,sourceHash);
 return {...attributed,platforms:['tiktok'],visibility:'private'};
}

/** Explicit operator authorization: public TikTok. Naive rejects its visibility field for TikTok. */
export function publicTikTokTestPost(fileId:string,clip:ClipEvidence,sourceHash:string):SocialPostCreate {
 const {visibility,...post}=privateTikTokTestPost(fileId,clip,sourceHash);
 return post;
}

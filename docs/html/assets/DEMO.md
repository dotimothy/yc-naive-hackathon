# Published product demo

The website video is the 120-second customer/Outreach cut (revision 6), captured on 2026-11-08. It shows recorded product interactions, the Naive crew, earlier published TikTok outputs, and AgentMail Outreach. Counts and service status are from capture time. It is not live telemetry.

- Source SHA-256: `2f074a8fe9272df7e4cd2114d4b282d8c031ac25eb735b941458007c10ea213f`
- Website SHA-256: `40e92d8e0a69eccf6a602ad3c35abb30ed9ee6c13c27ad99aecd34c2386cd8a4`
- Website artifact: `playlattice-demo.mp4`, 19,176,317 bytes
- Format: H.264, 1920×1080, 30 fps, yuv420p; AAC audio; 120.000 seconds; faststart.
- Original music was composed procedurally for the demo. Captions and credits are burned into the recording.

Web conversion:

```sh
ffmpeg -i playlattice-launch-120s-customer.mp4 -c:v libx264 -preset fast -crf 26 -pix_fmt yuv420p -c:a aac -b:a 128k -movflags +faststart playlattice-demo.mp4
```

Source references retained from the capture workflow: [Basquete — Gustavo Serrate](https://commons.wikimedia.org/wiki/File:Basquete.webm), CC BY 3.0; [ČBF 3x3 — Honza Nový](https://commons.wikimedia.org/wiki/File:%C4%8CBF_3x3_Tour_22.6._%C3%9Ast%C3%AD_nad_Labem.webm), CC BY 3.0; [TrackID3x3 — Kazuhiro Yamada et al.](https://github.com/open-starlab/TrackID3x3), CC BY 4.0. The showcase records previously published posts on the PlayLattice account; their attribution remains visible in the content. Clip selection, reframing, overlays, timing, and music are transformations. No endorsement by footage creators, TikTok, or sponsors is implied.

# PlayLattice project page

Static, self-contained project page. No backend, package installation, or build step required. Assets and CSS use relative paths for GitHub Pages project URLs.

Preview from the repository root:

```sh
.venv/bin/python scripts/serve_website.py --port 8080
```

Open http://127.0.0.1:8080. This does not start the analysis app. Use the repository preview server rather than `python -m http.server`: it supports HTTP byte ranges, which browsers need to seek reliably within the demo. Install the Python dependencies with `./setup.sh` first.

## Production hosting

Live Cloudflare Pages site: **https://playlattice-project.pages.dev/**.

Deploy updates from the repository root with `./scripts/deploy_pages.sh` after authenticating with Wrangler. The Pages project uses `develop` as its production branch; this is a direct-upload project, so GitHub pushes alone do not publish it. See [Cloudflare deployment instructions](../CLOUDFLARE_PAGES.md).

The existing Sites URL remains available independently. Both hosts serve the static project website; the Film Room application and video-analysis services remain on the host Mac.

## Product demo

The page embeds the product demo from [YouTube](https://www.youtube.com/watch?v=Lu6JVhPCQiE) at `#demo`, using YouTube’s privacy-enhanced embed and a direct Watch On YouTube link. The responsive player supports fullscreen. Previous local MP4/HLS demo assets remain archived in the repository but are no longer loaded by the page; see `assets/DEMO.md` for their provenance.

The page uses the app’s bundled Barlow fonts and broadcast theme. `page.js` handles accessible tabs and a clearly labeled quiz interaction example. Motion honors the system preference; the recorded GIF has a still-image fallback.

The dashboard GIF is the existing recorded app tour, not the new two-minute demo or the latest UI. Its underlying footage is TrackID3x3, CC BY 4.0. Attribution is visible on the page; capture and transformation details are in `docs/assets/README.md`. The quiz court illustration demonstrates the interaction format; it is not model output.

## Architecture page

`architecture.html` presents the current system map, video lifecycle, AgentMail → Naive outreach → validated source-clip preview branch, data/control boundaries, and the distinction between static Cloudflare Pages hosting and the live Mac app. Open `/architecture.html` on the same preview server.

## TikTok visual fallback

`assets/tiktok-quiz-preview.gif` is an 8 fps, 300 px-wide, silent recording of a previously published portrait quiz (TikTok post 7705723023252376845), not a live embed or the newest shot-quiz format. Source footage: Singpore amatuer basketball channel, https://www.youtube.com/watch?v=zpT8lc2vtkQ (LOW-008). Source credit is displayed beside the preview. The GIF is cropped/rendered quiz output with added graphics; `tiktok-quiz-poster.png` provides a reduced-motion still. The original profile embed remains expandable below it.

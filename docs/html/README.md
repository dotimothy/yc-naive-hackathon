# PlayLattice project page

Static, self-contained project page. No backend, package installation, or build step required. Assets and CSS use relative paths for GitHub Pages project URLs.

Preview from the repository root:

```sh
.venv/bin/python scripts/serve_website.py --port 8080
```

Open http://127.0.0.1:8080. This does not start the analysis app. Use the repository preview server rather than `python -m http.server`: it supports HTTP byte ranges, which browsers need to seek reliably within the demo. Install the Python dependencies with `./setup.sh` first.

## Publish later

Nothing is published automatically by adding these files.

**Publish from a branch:** In repository Settings → Pages, choose Deploy from a branch, select the branch containing this page and the `/docs` folder. The page will be at `https://dotimothy.github.io/yc-naive-hackathon/html/` after publication. GitHub's branch-based folder choices are `/` and `/docs`; `/docs/html` is not a selectable publishing root.

**Publish at the site's root:** Use a GitHub Pages Actions workflow that uploads `docs/html` with `actions/upload-pages-artifact`, then deploys it with `actions/deploy-pages`. Set Pages to GitHub Actions first. No deployment workflow is enabled by this change.

## Product demo

The page embeds the completed two-minute demo at `#demo`, with native playback controls, a poster image, segmented HLS for reliable seeking, and a direct MP4 link. Safari uses native HLS; supported Chrome/Firefox browsers use the bundled hls.js player. `assets/playlattice-demo.mp4` is a compressed H.264/AAC web derivative of the current customer/Outreach cut. This is the one explicitly published video exception in `.gitignore`; source footage and other generated videos remain ignored. See `assets/DEMO.md` for provenance, checksums, and source credits.

The page uses the app’s bundled Barlow fonts and broadcast theme. `page.js` handles accessible tabs and a clearly labeled quiz interaction example. Motion honors the system preference; the recorded GIF has a still-image fallback.

The dashboard GIF is the existing recorded app tour, not the new two-minute demo or the latest UI. Its underlying footage is TrackID3x3, CC BY 4.0. Attribution is visible on the page; capture and transformation details are in `docs/assets/README.md`. The quiz court illustration demonstrates the interaction format; it is not model output.

## Architecture page

`architecture.html` presents the current system map, video lifecycle, AgentMail → Naive outreach → validated source-clip preview branch, data/control boundaries, and the distinction between static GitHub Pages hosting and the live Mac app. Open `/architecture.html` on the same preview server.

## TikTok visual fallback

`assets/tiktok-quiz-preview.gif` is an 8 fps, 300 px-wide, silent recording of a previously published portrait quiz (TikTok post 7705723023252376845), not a live embed or the newest shot-quiz format. Source footage: Singpore amatuer basketball channel, https://www.youtube.com/watch?v=zpT8lc2vtkQ (LOW-008). Source credit is displayed beside the preview. The GIF is cropped/rendered quiz output with added graphics; `tiktok-quiz-poster.png` provides a reduced-motion still. The original profile embed remains expandable below it.

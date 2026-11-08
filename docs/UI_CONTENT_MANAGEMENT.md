# Workflow Overview and Video Previews

The main UI's Naive Crew tab shows a connected five-stage flow, batch-only progress counts, stage/outcome filters, current video, recent posts, crew responsibilities and spend. Live telemetry and posting health have separate indicators. Historical jobs and technical identifiers live in expandable sections. Provider-blocked work is shown as waiting rather than active.

The entry splash animates the existing logo, a basketball, a video trim and a portrait clip in 3.4 seconds. Skip Intro and Escape dismiss it. Reduced-motion users see a brief static introduction; keyboard focus and scroll are restored afterward. The application loads underneath without starting video analysis.

The Game Library, current workflow video and batch list use cached JPEG stills from registered video sources. Images load lazily; unavailable or not-yet-imported sources use a neutral placeholder. Thumbnail generation is bounded and runs no model inference.

A compact library-status endpoint replaces the previous per-game full-history fan-out. Badge polling pauses while the Naive view is active. This resolves browser connection starvation of the four-second workflow feed.

Validation: frontend production build; three thumbnail/library API tests; live isolated Chrome checks for source thumbnails, all five flow steps, 15 paginated previews, mobile page width, and three workflow polls in 13 seconds with no hidden library polling. Splash checks covered automatic completion, Skip, Escape and reduced motion. Claude's broader live checks also covered stage/outcome filters, reset, desktop/mobile layout and stale/offline behavior. No publishing policy was changed by these UI changes.

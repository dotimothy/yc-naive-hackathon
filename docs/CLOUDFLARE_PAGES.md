# Cloudflare Pages deployment

Production URL: **https://playlattice-project.pages.dev/**.

The standalone Pages project serves only `docs/html/`: the project website, architecture, demo video and segmented player assets. It does not host the Python analysis app, local databases, Naive workers, or credentials.

Configuration is `wrangler.jsonc`. The pinned deployment script uses Wrangler4.158.0 and deploys the `develop` branch as production. No build is needed for this static website. The project was explicitly created on Cloudflare Pages. Subsequent deployments use the existing Pages project directly.

```sh
# Authenticate in the browser once; credentials are stored outside the repository.
npx --yes wrangler@4.158.0 login

# Existing projects only; first project creation is handled during initial onboarding.
./scripts/deploy_pages.sh
```

For a deliberately separate branch preview, set `PLAYLATTICE_PAGES_BRANCH` to a branch other than the project's production branch. A direct-upload project is deployed with this script; pushing to GitHub alone does not publish it.

The embedded demo uses HLS with native Safari playback or bundled hls.js, with a direct MP4 fallback. `_headers` supplies playlist/segment MIME types and revalidation. Private media outside `docs/html/`, local databases, and secrets are not uploaded.

The existing Sites deployment is retained independently. Cloudflare authentication and account access are required for subsequent deployment commands.

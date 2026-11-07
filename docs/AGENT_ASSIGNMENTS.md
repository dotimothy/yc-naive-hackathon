# Initial agent assignments

Product scope: sports video intelligence, starting with basketball.
Build order: working local core → team acceptance → Naive SDK agents → optional sponsors.

## Shared baseline

The plan branch already contains AGENTS.md, README.md, docs/PROTOTYPE_PLAN.md,
and docs/API_CONTRACT.md. Read these before implementation.

## Claude: video pipeline

Branch: feature/cv-pipeline, based on develop; use a separate worktree.
Own workers/video/, workers/render/, backend/app/cv/, backend/tests/video/.
Implement process_video and render_clip using the shared contract.
First handoff: a real tracking batch from authorized local basketball footage.
Then deliver source-timestamped candidates, two playable clips, independent
ffprobe QA, hashes, exact run commands and measured performance.
Request root dependency/shared model changes through the integration lead.
Do not edit backend routes, persistence, frontend or install sponsor SDKs.

## Codex: platform and integration

Branch: feature/local-platform, based on develop; use a separate worktree.
Own backend API/services/database, frontend, and integration configuration.
First freeze canonical models and a labeled JSON fixture, then implement
upload → persisted job → worker adapter → SSE progress → tracking dashboard
→ clip viewer. Integrate Claude's first real batch immediately.
Only the integration lead changes contracts, root dependencies and migrations.

## Collaboration and release

Feature PRs target develop. Planning updates land on plan and are brought
into develop as needed. Merge develop into main after acceptance checks.
Each handoff records commit, changed files, input/output example, exact
commands, checks run and limitations. Never overwrite another agent's work.

No framework, Naive integration or sponsor account is required for Stage 1.
Fixtures unblock development but do not prove basketball recognition.
Missing authorized footage must be reported, never replaced with fabricated results.

## Next implementation action

In the main conversation, create the two isolated worktrees, launch Claude
for video and Codex for platform, and start the shared model/fixture baseline.
Claude CLI is installed at /Users/tim/.local/bin/claude; authentication and
runtime availability still need verification before launching work.

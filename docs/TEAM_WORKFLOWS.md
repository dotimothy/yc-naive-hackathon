# Reusable agent team workflows

Status: operating playbook and templates, not a running agent scheduler.
Project instructions in AGENTS.md and API_CONTRACT.md remain authoritative.
Use this playbook with Claude, Codex or another coding agent; roles describe
ownership, not a requirement to use a particular model/vendor.

## Team structure

| Team | Lead / initial implementer | Owns | Consumer |
| --- | --- | --- | --- |
| Video intelligence | Claude | workers/video/, workers/render/, backend/app/cv/, backend/tests/video/ | Platform |
| Platform | Codex | backend/app/api/, services/, db/, agents/, backend/tests/api/ | Experience |
| Experience | Codex initially; separate agent when useful | frontend/ | Integration |
| Integration and quality | Main-session lead + a reviewer per milestone | shared models, root dependencies, migrations, fixtures, docs, CI | All teams |

One agent may hold multiple roles; give each task exactly one owner. Start
with Claude/video and Codex/platform+experience. Add agents only for ready,
independent tasks; do not spend the first prototype building team tooling.
Reviewers report findings and evidence; they do not edit another team's files.
The integration lead serializes shared changes and merges. No concurrent
writers on the same worktree, files or dependency lockfile.

## Workflow 1: plan and dispatch

1. Read the accepted plan, check repository state and identify required inputs.
2. Lead creates a task from templates/TASK.md with allowed paths, contract
   version, base commit, inputs, acceptance commands and receiving team.
3. Create feature branches from develop in isolated worktrees in the main
   conversation. Bring accepted plan changes into develop before dispatch.
4. Freeze shared models and a labeled fixture first. Workers and UI then
   implement against that fixture independently.
5. Owner claims the task in its durable task record. Coordinator alone edits
   a shared task board; each owner writes only its own task/handoff record.

Task lifecycle: proposed → ready → claimed → implementing → review →
integrating → accepted. blocked records the missing input and resume condition;
a failed check returns to implementing. Never treat an unrun check as passed.
Only the lead marks accepted after evidence and consumer integration pass.

## Workflow 2: build and exchange

1. Implement one narrow deliverable and checkpoint a small commit.
2. Produce a handoff using templates/HANDOFF.md; include exact commit, inputs,
   outputs, commands, real test results and unresolved limitations.
3. Receiving team reproduces the example against the stated contract before
   acknowledging acceptance. Log incompatibilities in the task record.
4. Propose contract changes to the lead with compatibility/migration impact.
   Lead updates canonical models/fixture, then tells all affected consumers.

Dependency order: shared baseline → parallel video/API/UI → first actual
track batch integrated → two verified clips → private consented profile,
retry/audit/report → complete local acceptance. Fixtures can unblock work;
the acceptance path must consume real worker output.

## Workflow 3: integrate and release

Feature branches → reviewed PRs into develop → integrated acceptance → main.
Planning changes use plan → reviewed PR into develop. Never merge develop
back into plan just to carry application code. Avoid duplicate cherry-picks.

Before merging, lead checks current develop, ownership, contract compatibility
and meaningful checks. Resolve conflicts in the feature branch and recheck.
Integrated acceptance covers actual tracking in the UI, two source-verified
clips, persisted jobs/audit, bounded failure recovery and consent boundaries.
Release is a separate explicit decision by the release owner. Use a revert
commit for a bad integration; do not reset or force-push shared branches.
These are team conventions until repository rules/CI enforce them.

## Workflow 4: flush context and resume

Here “flush” means end/restart agent sessions while retaining work and evidence.
It never means deleting branches, resetting Git or discarding private data.

1. Stop dispatching new tasks; each active owner finishes a safe checkpoint.
2. Commit only intended files. If a partial state cannot be committed safely,
   record exact worktree/paths and leave them intact; do not assign a new
   writer there until the previous owner is stopped and the lead reviews it.
3. Complete a handoff containing HEAD/base SHA, dirty-state inventory, task
   status, contracts, checks, blockers and the first next command.
4. Lead records which sessions are stopped and which tasks remain claimed;
   release a claim only after its writer has stopped. Then close sessions.
5. Fresh agent reads AGENTS.md, task and handoff; verifies Git state and
   artifact availability; reproduces the smallest relevant check and resumes.

Store durable task records under docs/tasks/<task-id>.md and handoffs under
docs/handoffs/<task-id>.md. Exclude secrets, videos, rosters and private athlete
records. Reference local/private artifacts by registered ID/hash and an
authorized retrieval process, not credentials or public links.

## Workflow 5: integrate another company team

Copy this playbook and templates, then fill templates/TEAM.json for that team.
Name a provider lead and consumer lead; record an owned module/service boundary,
a versioned interface, fixture, acceptance command and escalation contact.
Use adapters at the boundary; company teams can retain their own internal
models, tools and repositories. Cross-repo handoffs pin repository + commit
and interface version, with an example payload and compatibility expectations.

For a breaking interface change, agree a migration window and retain the old
adapter until consumers migrate. Each team keeps its own backlog and release
owner. Cross-team acceptance requires both leads' evidence; it does not grant
permission to access another team's systems or send external communications.

ScoutFilm ordering remains local core → team acceptance → Naive agents →
optional sponsors. This company workflow does not move those gates earlier.

## Agent skills

### Issue tracker

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`.
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`.
- **Close**: `gh issue close <number> --comment "..."`.

Infer the repository from `git remote -v`; `gh` does this automatically inside the clone.

**PRs as a request surface: no.** External pull requests do not enter the issue triage queue.

GitHub shares one number space across issues and pull requests. Resolve an ambiguous `#42` with `gh pr view 42`, then fall back to `gh issue view 42`.

#### Skill routing

- When a skill says **publish to the issue tracker**, create a GitHub issue.
- When a skill says **fetch the relevant ticket**, run `gh issue view <number> --comments`.

#### Wayfinding operations

- A map is one issue labelled `wayfinder:map`; child tickets use `wayfinder:<type>`.
- Prefer GitHub sub-issues and native issue dependencies. If unavailable, use task lists and a `Blocked by: #<n>` line.
- Claim work with `gh issue edit <n> --add-assignee @me`.
- Resolve work by commenting with the answer, closing the issue, and recording a context pointer in the map.

### Triage labels

The engineering skills use five canonical roles. This repository uses the default strings.

| Canonical role | GitHub label | Meaning |
|---|---|---|
| `needs-triage` | `needs-triage` | Maintainer evaluation is required |
| `needs-info` | `needs-info` | Waiting for more information from the reporter |
| `ready-for-agent` | `ready-for-agent` | Fully specified and ready for an autonomous agent |
| `ready-for-human` | `ready-for-human` | Requires human implementation |
| `wontfix` | `wontfix` | Will not be actioned |

When a skill names a canonical role, use the corresponding GitHub label from this table.

### Domain docs

This repository uses a single domain context.

#### Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- Read ADRs under `docs/adr/` that affect the area being changed.
- If either location does not exist, proceed silently. Domain-modeling workflows create them lazily when terminology or architectural decisions are resolved.

#### Vocabulary

Use the glossary terms in `CONTEXT.md` in issues, tests, hypotheses, and implementation. If a needed concept is absent, reconsider whether it is repository language or note the gap for domain modeling.

#### ADR conflicts

Surface any conflict with an existing ADR explicitly rather than silently overriding it.

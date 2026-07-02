# Subagents for this project

Claude Code loads project-scoped subagent persona files from `.claude/agents/`.
This directory mirrors the category layout used by
[VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents),
but **only one file here is actually from that upstream repo**:

| File | Source |
|---|---|
| `01-core-development/backend-developer.md` | Verbatim from VoltAgent/awesome-claude-code-subagents, MIT License, Copyright (c) 2025 VoltAgent. See `LICENSE-voltagent-subagents` in this directory. |
| `02-email-outreach/cold-email-drafter.md` | Custom-authored for this project. |
| `02-email-outreach/email-compliance.md` | Custom-authored for this project. |
| `03-infrastructure/github-manager.md` | Custom-authored for this project. |
| `03-infrastructure/vercel-deployer.md` | Custom-authored for this project. |
| `04-quality-security/stripe-checkout.md` | Custom-authored for this project. |
| `09-meta-orchestration/human-takeover.md` | Custom-authored for this project. |
| `10-research-analysis/lead-researcher.md` | Custom-authored for this project. |

## Why only one file is "real"

The upstream repo's category names and file lists were checked directly
against the live repository before writing anything here. It has
`02-language-specialists` (not `02-email-outreach`), and none of
`cold-email-drafter.md`, `email-compliance.md`, `github-manager.md`,
`vercel-deployer.md`, `stripe-checkout.md`, `human-takeover.md`, or
`lead-researcher.md` exist there -- those roles are specific to this
pipeline's domain (cold email compliance, GitHub/Vercel/Stripe hand-off,
human-in-the-loop negotiation gating), not generic enough to be in a
general-purpose collection. Rather than fabricate content and attribute it
to VoltAgent, the seven files above were written fresh for this project,
in the same frontmatter/style convention as the one real file, and each
says so explicitly in a footer note.

`01-core-development/backend-developer.md` genuinely exists upstream and
is reproduced verbatim under its MIT license.

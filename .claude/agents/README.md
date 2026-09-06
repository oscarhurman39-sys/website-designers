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
| `02-email-outreach/client-assets.md` | Custom-authored for this project (prospect photos/logo: offer, inbound attachments, in-place rebuild). |
| `03-infrastructure/github-manager.md` | Custom-authored for this project. |
| `03-infrastructure/vercel-deployer.md` | Custom-authored for this project. |
| `04-quality-security/stripe-checkout.md` | Custom-authored for this project. |
| `09-meta-orchestration/autonomy-guardrails.md` | Custom-authored for this project (formerly `human-takeover.md`; renamed when the manual takeover flow was replaced by code-enforced guardrails). |
| `10-research-analysis/lead-researcher.md` | Custom-authored for this project. |

## Why only one file is "real"

The upstream repo's category names and file lists were checked directly
against the live repository before writing anything here. It has
`02-language-specialists` (not `02-email-outreach`), and none of
`cold-email-drafter.md`, `email-compliance.md`, `github-manager.md`,
`vercel-deployer.md`, `stripe-checkout.md`, `autonomy-guardrails.md`, or
`lead-researcher.md` exist there -- those roles are specific to this
pipeline's domain (cold email compliance, GitHub/Vercel/Stripe hand-off,
code-enforced autonomous-negotiation guardrails), not generic enough to be
in a general-purpose collection. Rather than fabricate content and attribute it
to VoltAgent, the seven files above were written fresh for this project,
in the same frontmatter/style convention as the one real file, and each
says so explicitly in a footer note.

`01-core-development/backend-developer.md` genuinely exists upstream and
is reproduced verbatim under its MIT license.

## StarNet skill overlay

The seated StarNet Agency-room agents are wired in `docs/STARNET_SKILL_WIRING.md`. These repo-local persona files should follow that overlay when invoked through StarNet:

- use `Make a Plan` before non-trivial source, template, schema, deploy, payment, or process changes;
- use `Creative Ideation` only for bounded copy/offer/niche/concept exploration;
- treat `Popular Web Designs` as prototype/design-direction input, not direct production template authority;
- keep `ASCII Art` internal unless the Commander explicitly requests it in a client-facing artifact;
- do not depend on the withheld saved `website_designers_pipeline` skill until the Commander approves it in `ABILITIES > SKILLS`.

The overlay does not permit live sourcing, live sending, Stripe live mode, outbound-volume changes, or bypassing FINN's verification gate.


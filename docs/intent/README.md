# Intent docs

One file per initiative, capturing **what is wanted, why, and under what constraints** —
written before the design or the code, and corrected as reality pushes back.

Adapted from Anthropic's AI-Native SDLC playbook, which chains
`intent.md → spec.md → plan.md → diff → review`. We deliberately take only the first
link. The rest of that chain already exists here under different names:

- **Spec layer** — the feature docs in `docs/` (`trade-route-tracker.md`,
  `start-route-tool.md`, `presence-layer.md`).
- **Plan layer** — `docs/todo.md`.

Adding parallel `spec.md`/`plan.md` files would create a second place for the same
information to go stale, which is the problem, not the fix.

## Why these exist

The reasoning behind decisions in this project has been living in chat transcripts and
assistant memory — outside the repo, un-greppable, gone on a fresh clone. The *what* is in
the code and the *how* is in `docs/`, but the *why* evaporates. These files are where it
goes instead.

Observed failure mode they're meant to prevent: `start-route-tool.md` described a
resolve-by-name design while what shipped was token-handoff; `todo.md` marked
`start_trade_run` done before it had ever run live. Intent drifting from implementation,
with nothing recording which one moved.

**The bar is "we don't re-evaluate this later."** That's the whole test. A decision is
recorded well enough when it stops the same question being reopened months from now
because nobody remembers what settled it.

Practically that means recording **what was rejected and on what evidence**, not just what
was chosen — the rejected option is what gets re-proposed, and the evidence is the only
thing that closes it again quickly. "We use a VLM" invites the question; "classical OCR
tested at 80% because the UI is semi-opaque and head-tilt moves text out of the crop"
answers it before it's asked.

These are **not** portfolio pieces, even though portfolio material can be extracted from
them later. Writing for a hypothetical reviewer produces padding and performed rigor;
writing to stop yourself chasing your own tail produces the evidence that would actually
change your mind back, and nothing else. Extract when needed; don't write for it.

## What belongs in one

- **Problem** — what's wrong now, in plain terms.
- **Desired outcome** — what's true when this is done.
- **Constraints** — budget, hardware, legal, time, things already decided elsewhere.
- **Decided** — settled calls *with the reason*, especially where evidence overruled an
  assumption. This is the highest-value section; it's what stops a decision being
  relitigated in six months.
- **Open questions** — known gaps. An intent doc with no open questions is usually lying.

Not a spec. No schemas, no function signatures, no task breakdown — those go in `docs/`
and `todo.md` respectively.

## Where documents live

Repo or Google Docs, decided by one test: **would this document become *wrong* because of
a code change?**

- **Yes → the repo.** Intent docs, design/spec docs, the roadmap. These go stale the
  moment the code moves, so they need to be versioned with it, reviewable in the same
  diff, and greppable without leaving the editor. Drift is the failure mode, and
  co-location is the only thing that reliably catches it.
- **No → outside (Docs/Drive).** Source material, course writeups, meeting notes, anything
  with heavy diagrams, anything a non-engineer edits or comments on. A conversation
  transcript can't be made wrong by a refactor, and Google Docs is better at the things
  those documents actually need — comments, collaborators, images.

**Never reference an external document as if it were reachable.** A path like
`~/Documents/something.docx` is a dead pointer for everyone but its author. If an external
source is load-bearing, restate the load-bearing part here and cite the source as
provenance. If it isn't load-bearing, don't cite it.

## Conventions

- Date any status or decision line. This project's docs have accumulated undated strata
  before; don't add more.
- When reality contradicts an intent doc, **edit the doc**. A stale intent file is worse
  than none.
- Link out to the spec doc that implements it, once one exists.

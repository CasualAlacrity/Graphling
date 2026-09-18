# Intent — classify_topic rework

**Status:** next thing to build (2026-09-18). Small, self-contained, and the first
initiative to go through this intent-doc practice.

## Problem

`classify_topic` (`app/graph.py`) returns **user-facing text** in
`TopicClassification.decline_line_str`, and `decline_topic` speaks it verbatim into an
`AIMessage` with zero validation.

That produced the `self_aware_meta` leak seen in live testing: the classifier returned a
raw category *tag* instead of resolved text — violating its own prompt instruction — and
ALICE said the tag out loud. `decline_topic` has no way to catch it, because it can't tell
a tag from a sentence.

Separately, the canned decline lines are flavourless. They read as a guardrail firing
rather than as ALICE, which undercuts the persona at exactly the moment the pilot is being
told no.

Note: the original design was built knowing a smaller model was coming, and was cheap to
leave rough because the LLM calls were on a school API key. Neither of those holds now.

## Desired outcome

The classifier **decides**; it never words anything. Decline text is generated in-persona
at the moment of decline — tight (a sentence or two), on-brand, not canned.

This matters beyond flavour: it makes the `self_aware_meta` class of bug **impossible by
construction** rather than defended against. With no tag-to-text resolution step, there's
nothing to leak.

## Decided

- `TopicClassification` returns `on_topic: bool` and `reason: str` only. Drop
  `decline_line_str` entirely. `reason` is for tracing and debugging and must never reach
  a user.
- `decline_topic` becomes a small persona-aware generation taking the classifier's
  `reason` as context.
- Delete the `DeclineLine` model — declared in `graph.py` and referenced nowhere, left
  over from the canned-line design.
- **Classifier stays on a cheap hosted model (GPT-4o-mini); decline generation uses the
  local chat model.** Structured output is where small local models are least reliable,
  the classifier's work is tiny, and keeping it hosted leaves VRAM entirely for the chat
  model. First real consumer of per-role model selection (see
  `docs/intent/model-stack.md` if/when written).

## Constraints

- Extra LLM call lands **only on the decline path**, which is rare. Happy-path latency is
  unchanged.
- **Don't shape length with `max_tokens`.** Instruct brevity in the prompt and use
  `max_tokens` only as a generous backstop. A hard token stop truncates mid-sentence, and
  through TTS that sounds like ALICE simply stopped talking — far worse than a truncated
  line looks on screen.
- Per `feedback-ask-before-editing`: graph nodes and structured-output schemas are Jeff's
  to write. Claude sketches the shape.

## Open questions

- **Does the decline generator see the pilot's raw message, or only `reason`?** This is
  the real risk in the design — asking a model to write *about* refusing a topic is one
  weak prompt away from it partially answering the thing being refused. Passing only
  `reason` is safer; passing the message gives better-targeted wording. If the message is
  passed, the prompt needs an explicit instruction never to address its content.
- Which persona subset the decline call gets — full persona template, or a trimmed
  variant? Full costs tokens on every decline; trimmed risks drifting off-voice.
- Does `reason` need a constrained vocabulary for tracing/analytics, or is free text fine?
  Free text is simpler and can't leak (it's never spoken) — but a rough category makes it
  possible to see *what* ALICE declines most, which is useful later.
- The deeper question deferred from live testing: the classifier misfired on "Let's do
  it." after a failed search, treating a conversational commitment as off-topic. It has no
  signal for pending confirmation state. Fixing the decline *wording* doesn't fix the
  decline *decision* — that's a separate diagram/design pass Jeff planned to draw.

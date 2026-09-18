"""Pilot slang → catalog names.

Nicknames are a *closed* set: "connie", "cat", "MSR" are known, stable, community-wide
terms. That makes them a lookup, not an inference problem — a dict resolves them
correctly by construction, with no latency and no chance of the confident-wrong-answer
failure an LLM resolver produced in testing ("banana" -> "Buccaneer").

This is deliberately the only layer that encodes domain slang. The phonetic fallback in
matching.py handles the *open-ended* problem — the unbounded ways speech-to-text can
garble a name — which can't be enumerated and needs an algorithm instead.

**Aliases map to the least specific correct name.** "connie" becomes "Constellation", not
"Constellation Andromeda", so fuzzy matching surfaces the variants and ALICE asks which
one. Silently picking a variant would be deciding something the pilot didn't say.

SEED DATA — needs a domain pass. These are the entries I'm confident about; extend from
real transcripts rather than guesswork, because a wrong alias produces a confident wrong
match, which is exactly what the rest of this layer exists to prevent.
"""

# Keyed by the `label` resolve_or_hedge is already called with ("ship", "commodity",
# "location"), so slang stays scoped to its own vocabulary — "cat" is a Caterpillar, and
# should never resolve a commodity.
ALIASES: dict[str, dict[str, str]] = {
    "ship": {
        "connie": "Constellation",
        "cat": "Caterpillar",
        "cutty": "Cutlass",
        "msr": "Mercury Star Runner",
        "herc": "Hercules Starlifter",
        "prospy": "Prospector",
        "starfarer": "Starfarer",
        "banu merchantman": "Merchantman",
    },
    "commodity": {
        "quant": "Quantainium",
        "agri": "Agricium",
        "rmc": "Recycled Material Composite",
        "cmat": "Construction Materials",
    },
    "location": {
        "a18": "Area18",
        "new bab": "New Babbage",
        "baijini": "Baijini Point",
        "everus": "Everus Harbor",
        # UEX records the station as its full legal name, so the abbreviation everyone
        # actually says resolves to nothing without this.
        "grimhex": "Green Imperial Housing Exchange",
        "grim hex": "Green Imperial Housing Exchange",
    },
}


def canonicalize(query: str, label: str) -> str:
    """Rewrites pilot slang to a catalog-shaped name, leaving anything unrecognised
    untouched. Matching is whole-string and case-insensitive — a substring rule would
    rewrite the "cat" inside "Caterpillar" and corrupt correct input."""
    table = ALIASES.get(label)
    if not table:
        return query

    return table.get(query.strip().lower(), query)

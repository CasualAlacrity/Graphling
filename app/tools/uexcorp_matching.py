from typing import TypeVar

from rapidfuzz import process

_HasNameAndCode = TypeVar("_HasNameAndCode")


def match_by_name_or_code(query: str, items: list[_HasNameAndCode], score_cutoff: int = 60) -> _HasNameAndCode | None:
    choices = []
    lookup = []
    for item in items:
        choices.append(item.name)
        lookup.append(item)

        code = getattr(item, "code", None)
        if code:
            choices.append(code)
            lookup.append(item)

    match = process.extractOne(query, choices, score_cutoff=score_cutoff)
    return lookup[match[2]] if match else None


def filter_by_match(rows, query, candidates, attr):
    if not query:
        return rows
    match = match_by_name_or_code(query, candidates)
    return [r for r in rows if getattr(r, attr) == match.name] if match else rows

"""Shared, deterministic restrictions for generation, recipes and replacements."""

import unicodedata
from collections.abc import Iterable


def ingredient_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def excluded_matches(exclusions: Iterable[str], names: Iterable[str]) -> list[str]:
    selected = {ingredient_key(v) for v in exclusions}
    return sorted({ingredient_key(v) for v in names} & selected)


def allergy_matches(selected: Iterable[str], declared: Iterable[str]) -> list[str]:
    declared_set = set(declared)
    result = []
    for value in selected:
        family = {value}
        if value == "shellfish":
            family |= {"crustaceans", "molluscs"}
        if value in {"crustaceans", "molluscs"}:
            family.add("shellfish")
        if family & declared_set:
            result.append(value)
    return sorted(result)

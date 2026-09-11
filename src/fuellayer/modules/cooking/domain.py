import hashlib
import json
import re
from decimal import Decimal
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def convert(quantity: float, source: str, target: str) -> Decimal | None:
    groups = {
        "g": ("mass", 1),
        "kg": ("mass", 1000),
        "ml": ("volume", 1),
        "l": ("volume", 1000),
        "item": ("count", 1),
        "items": ("count", 1),
        "pièce": ("count", 1),
        "pièces": ("count", 1),
    }
    amount = Decimal(str(quantity))
    source, target = source.strip().lower(), target.strip().lower()
    if source == target:
        return amount
    a, b = groups.get(source), groups.get(target)
    return amount * a[1] / b[1] if a and b and a[0] == b[0] else None


def timer_candidates(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    pattern = (
        r"(?<![\w.,/+\-])(\d+(?:[.,]\d+)?)\s*(?:(?:[–-]|to|à)\s*(\d+(?:[.,]\d+)?)\s*)?"
        r"(seconds?|secs?|secondes?|minutes?|mins?|heures?|hours?)\b"
    )
    for step in steps:
        for index, match in enumerate(re.finditer(pattern, step["text"], re.I)):
            factor = (
                3600
                if match[3].lower().startswith(("h",))
                else 60
                if match[3].lower().startswith("m")
                else 1
            )
            low = float(match[1].replace(",", ".")) * factor
            high = float((match[2] or match[1]).replace(",", ".")) * factor
            if low.is_integer() and high.is_integer() and 1 <= low <= high <= 86400:
                result.append(
                    {
                        "id": f"{step['id']}:{index}",
                        "step_id": step["id"],
                        "excerpt": match[0],
                        "minimum": int(low),
                        "maximum": int(high),
                    }
                )
    return result

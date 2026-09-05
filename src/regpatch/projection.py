"""Pure XML projection shared by causal admission and scoring; no acquisition or oracle I/O."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from defusedxml import ElementTree as SafeElementTree

_XREF_RE = re.compile(
    r"Link\s+to\s+(?:an\s+amendment|a\s+correction(?:\s+of\s+the\s+above\s+amendment)?)"
    r"\s+published\s+at\s+"
    r"(?P<volume>[0-9]+)\s+FR\s+(?P<page>[0-9]+),\s+"
    r"(?P<month>[A-Z][a-z]{2,8})\.?\s+(?P<day>[0-9]{1,2}),\s+(?P<year>[0-9]{4})",
    re.IGNORECASE,
)

_XREF_ID_RE = re.compile(r"[0-9]{8}")


def _element_digest(elem: Any | None) -> str | None:
    if elem is None:
        return None
    return hashlib.sha256(SafeElementTree.tostring(elem, encoding="utf-8")).hexdigest()


def _substantive_element_digest(elem: Any | None) -> str | None:
    """Hash the candidate-scored XML projection."""
    if elem is None:
        return None
    tokens: list[Any] = []

    def visit(node: Any) -> bool:
        if is_projected_editorial_node(node):
            return False
        if _local_name(str(node.tag)).upper() == "TABLE":
            tokens.append(("semantic-table", canonical_table_signature(node)))
            return True
        tokens.append(
            (
                "start",
                _local_name(str(node.tag)),
                tuple(sorted((str(key), str(value)) for key, value in node.attrib.items())),
                _normalized_text(node.text),
            )
        )
        for child in list(node):
            retained = visit(child)
            tail = _normalized_text(child.tail)
            if tail:
                tokens.append(("tail" if retained else "projected-tail", tail))
        tokens.append(("end", _local_name(str(node.tag))))
        return True

    visit(elem)
    encoded = json.dumps(tokens, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_table_signature(elem: Any) -> tuple[Any, ...]:
    """Return presentation-neutral, order-preserving semantics for one eCFR table."""
    captions = tuple(
        _joined_itertext(node)
        for node in elem.iter()
        if _local_name(str(node.tag)).upper() == "CAPTION" and _joined_itertext(node)
    )
    rows: list[tuple[Any, ...]] = []
    for row in elem.iter():
        if _local_name(str(row.tag)).upper() not in {"TR", "ROW"}:
            continue
        cells: list[tuple[Any, ...]] = []
        for cell in list(row):
            tag = _local_name(str(cell.tag)).upper()
            if tag not in {"TH", "TD", "ENTRY"}:
                continue
            spans = tuple(
                (name.lower(), str(value))
                for name, value in sorted(cell.attrib.items())
                if name.lower() in {"colspan", "rowspan", "namest", "nameend", "morerows"}
            )
            cells.append((tag, spans, _joined_itertext(cell)))
        rows.append(tuple(cells))
    return (captions, tuple(rows))


def _joined_itertext(elem: Any) -> str:
    return " ".join(part for value in elem.itertext() if (part := _normalized_text(str(value))))


def is_editorial_amendment_xref(elem: Any) -> bool:
    """Return true only for the exact synthetic eCFR pending-amendment link shape."""
    if _local_name(str(elem.tag)) != "XREF" or _XREF_RE.search(_text(elem)) is None:
        return False
    identifier = str(elem.attrib.get("ID", ""))
    reference_id = str(elem.attrib.get("REFID", ""))
    instruction = str(elem.attrib.get("AMDINSN", ""))
    if (
        _XREF_ID_RE.fullmatch(identifier) is None
        or not reference_id.isdigit()
        or (instruction and not instruction.isdigit())
    ):
        return False
    try:
        date.fromisoformat(f"{identifier[:4]}-{identifier[4:6]}-{identifier[6:]}")
    except ValueError:
        return False
    return True


def is_projected_editorial_node(elem: Any) -> bool:
    """Identify non-derivable eCFR editorial metadata excluded from reward."""
    return _local_name(str(elem.tag)).upper() == "CITA" or is_editorial_amendment_xref(elem)


def _text(elem: Any) -> str:
    return " ".join("".join(elem.itertext()).split())


def _normalized_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

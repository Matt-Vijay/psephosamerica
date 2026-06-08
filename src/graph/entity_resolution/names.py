"""Person-name parsing, normalization, and comparison.

The deterministic substrate for entity resolution: before any probabilistic
linker (Splink) or learned matcher (bi-encoder) can score two records as the
same person, names must be parsed into comparable parts and reduced to a
canonical form. "Jane M. Doe Jr.", "Doe, Jane", and "Smith, Bob James" all have
to line up. This module does the boring, high-leverage 80%:

* :func:`normalize_name_token` — accent-fold + lowercase + de-punctuate.
* :class:`PersonName` — parse free-text names (comma or space order, suffixes,
  particles, parenthetical/quoted nicknames) into structured, normalized parts.
* :func:`canonical_given_root` — fold common diminutives (Bob -> robert) so a
  nickname and a formal first name block and compare together.
* :func:`given_names_compatible` / :meth:`PersonName.comparison_vector` — the
  feature comparators a Fellegi-Sunter / Splink model consumes.

Deliberately dependency-free and deterministic so it is fully unit-testable and
the heavier matchers plug in above it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Generational suffixes. "v" is intentionally excluded: a lone "V" is far more
# often a middle initial than a suffix, and conflating them corrupts parsing.
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})

# Lowercase nobiliary / locational particles that belong to the surname rather
# than standing as their own token (e.g. "van Buren", "de la Cruz").
_PARTICLES = frozenset(
    {"van", "von", "de", "del", "della", "da", "di", "la", "le", "du", "den", "der", "ter"}
)

# Diminutive -> formal-root groups. Kept unambiguous: every variant maps to
# exactly one root. Extend freely; ambiguous variants (e.g. "ted" for both
# Edward and Theodore) are assigned to a single root on purpose.
_NICKNAME_GROUPS: dict[str, tuple[str, ...]] = {
    "robert": ("rob", "bob", "bobby", "robbie"),
    "william": ("will", "bill", "billy", "willie", "liam"),
    "james": ("jim", "jimmy", "jamie"),
    "john": ("jack", "johnny", "jon"),
    "richard": ("dick", "rick", "rich", "ricky", "richie"),
    "elizabeth": ("liz", "beth", "betsy", "eliza", "lizzie", "libby"),
    "anthony": ("tony",),
    "margaret": ("peggy", "meg", "maggie", "marge"),
    "charles": ("charlie", "chuck"),
    "michael": ("mike", "mikey"),
    "thomas": ("tom", "tommy"),
    "edward": ("ed", "eddie", "ted", "teddy"),
    "joseph": ("joe", "joey"),
    "daniel": ("dan", "danny"),
    "katherine": ("kate", "katie", "kathy", "kit"),
    "patricia": ("pat", "patty", "trish"),
    "christopher": ("chris",),
    "matthew": ("matt",),
    "andrew": ("andy", "drew"),
    "benjamin": ("ben", "benny"),
    "samuel": ("sam", "sammy"),
    "nicholas": ("nick", "nicky"),
    "theodore": ("theo",),
}

_NICKNAME_TO_ROOT: dict[str, str] = {
    variant: root for root, variants in _NICKNAME_GROUPS.items() for variant in variants
}

_NICKNAME_RE = re.compile(r'"([^"]+)"|\(([^)]+)\)|\'([^\']+)\'')


def normalize_name_token(raw: str) -> str:
    """Fold a name token to a comparison key: no accents, punctuation, or case.

    Hyphens and slashes become spaces (so "Smith-Jones" -> "smith jones");
    apostrophes and dots are dropped without a gap ("O'Brien" -> "obrien").
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    spaced = re.sub(r"[-/]", " ", without_accents.lower())
    cleaned = re.sub(r"[^a-z0-9 ]", "", spaced)
    return re.sub(r"\s+", " ", cleaned).strip()


def canonical_given_root(name: str) -> str:
    """Fold a given name to its formal root so diminutives unify.

    "Bob" and "Robert" both return "robert"; unknown names return their
    normalized form unchanged.
    """
    token = normalize_name_token(name)
    return _NICKNAME_TO_ROOT.get(token, token)


def given_names_compatible(a: str, b: str) -> bool:
    """True when two given names could denote the same person.

    Compatible iff they are equal, one is the other's leading initial, or they
    share a canonical diminutive root. Blank names are never compatible.
    """
    na, nb = normalize_name_token(a), normalize_name_token(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) == 1 or len(nb) == 1:
        return na[0] == nb[0]
    return canonical_given_root(na) == canonical_given_root(nb)


def _extract_suffix(tokens: list[str]) -> tuple[list[str], str | None]:
    """Strip a single trailing generational suffix, if present."""
    if tokens and normalize_name_token(tokens[-1]) in _SUFFIXES:
        return tokens[:-1], normalize_name_token(tokens[-1])
    return tokens, None


def _split_family(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split space-ordered tokens into (leading given+middle, trailing family).

    The family is the final token plus any immediately-preceding particles.
    """
    if not tokens:
        return [], []
    start = len(tokens) - 1
    while start - 1 >= 0 and normalize_name_token(tokens[start - 1]) in _PARTICLES:
        start -= 1
    return tokens[:start], tokens[start:]


@dataclass(frozen=True)
class NameComparison:
    """The feature vector comparing two :class:`PersonName` records."""

    family_exact: bool
    given_compatible: bool
    middle_compatible: bool
    suffix_conflict: bool


@dataclass(frozen=True)
class PersonName:
    """A person name parsed into normalized, comparable parts."""

    given: str
    middle: tuple[str, ...]
    family: str
    suffix: str | None
    nickname: str | None
    raw: str

    @classmethod
    def parse(cls, raw: str) -> PersonName:
        text = raw.strip()
        nickname: str | None = None
        match = _NICKNAME_RE.search(text)
        if match is not None:
            captured = next(group for group in match.groups() if group is not None)
            nickname = normalize_name_token(captured) or None
            text = f"{text[: match.start()]} {text[match.end() :]}"

        family_first, _, remainder = text.partition(",")
        if remainder:  # "Family, Given Middle [Suffix]" order
            given_tokens, suffix = _extract_suffix(remainder.split())
            family_tokens = family_first.split()
            lead = given_tokens
        else:  # "Given Middle Family [Suffix]" order
            tokens, suffix = _extract_suffix(text.split())
            lead, family_tokens = _split_family(tokens)

        given = normalize_name_token(lead[0]) if lead else ""
        middle = tuple(normalize_name_token(tok) for tok in lead[1:] if normalize_name_token(tok))
        family = " ".join(
            normalize_name_token(tok) for tok in family_tokens if normalize_name_token(tok)
        )
        return cls(
            given=given,
            middle=middle,
            family=family,
            suffix=suffix,
            nickname=nickname,
            raw=raw,
        )

    def blocking_key(self) -> str:
        """A coarse candidate-generation key: ``family|given-root-initial``.

        Uses the canonical root's initial so "Bob Smith" and "Robert Smith"
        land in the same block.
        """
        root = canonical_given_root(self.given) if self.given else ""
        initial = root[0] if root else ""
        return f"{self.family}|{initial}"

    def comparison_vector(self, other: PersonName) -> NameComparison:
        """Compare two names into the boolean features a linker scores."""
        return NameComparison(
            family_exact=self.family == other.family and bool(self.family),
            given_compatible=given_names_compatible(self.given, other.given),
            middle_compatible=self._middle_compatible(other),
            suffix_conflict=(
                self.suffix is not None and other.suffix is not None and self.suffix != other.suffix
            ),
        )

    def _middle_compatible(self, other: PersonName) -> bool:
        # Absence of a middle name on either side never conflicts.
        if not self.middle or not other.middle:
            return True
        pairs = min(len(self.middle), len(other.middle))
        return all(given_names_compatible(self.middle[i], other.middle[i]) for i in range(pairs))

"""Feasibility rules for any profile: the evaluator as a decision tree over profile predicates.

The site lets a visitor describe their own profile (residence and ties with other countries)
instead of picking one of the curated ones in data/curated/profiles.yaml. The evaluator
(haggler.research.evaluate.assess) stays the only place the rules live: for one (service,
target) pair, `explore` runs it with a SymbolicProfile that answers nothing up front. Whenever the
evaluator asks a question about the profile (does it live in T? does it hold a document of CO?
how often is it in T?) the run stops and is replayed once per possible answer, so the result is a
tree whose inner nodes are those questions and whose leaves are the verdicts. The browser walks
the tree with the visitor's answers (web/src/logic/verdicts.ts); `evaluate_tree` here is the
Python twin the tests use to check that a curated profile walked through its tree gets exactly
the verdict `assess` gives it.

Questions (`ask` in the JSON, answers are strings):

  ["lives", CC]          lives in CC                        "true" | "false"
  ["lives_eea"]          lives in an EU/EEA country         "true" | "false"
  ["doc", CC]            holds an ID document of CC          "true" | "false"
  ["doc_any"]            holds any ID document               "true" | "false"
  ["doc_eea"]            holds an EU/EEA ID document         "true" | "false"
  ["doc_residence"]      holds an ID document of its residence  "true" | "false"
  ["phone", CC]          has a phone number of CC            "true" | "false"
  ["address", CC]        has a real address in CC (the residence counts)  "true" | "false"
  ["card", CC]           holds a card issued in CC           "true" | "false"
  ["presence", CC]       can be in CC                        "none" | "once" | "periodic"
  ["preset", ID]         the profile is exactly curated profile ID (same capabilities, see
                         `matches`); curated feasibility records only apply then

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from haggler.models.curated import BundleVerdictRecord, FeasibilityFile, FeasibilityRecord

Question = tuple[str, ...]
Tree = dict[str, Any]

_BOOL = ("true", "false")
_PRESENCE = ("none", "once", "periodic")
_CAPABILITY_LISTS = (
    "id_document_countries",
    "phone_countries",
    "address_countries",
    "card_countries",
)
# Exploration guard: a pair whose tree grows past this many runs is a bug in the evaluator.
MAX_RUNS = 20_000


class _Ask(Exception):
    def __init__(self, question: Question, answers: tuple[str, ...]) -> None:
        super().__init__(question)
        self.question = question
        self.answers = answers


class SymbolicProfile:
    """Answers the evaluator's profile predicates from `answers`; an unanswered one raises
    _Ask so `explore` can branch on it. Has no residence, documents or instruments of its own:
    a profile described only by its answers (cards by country, see Profile.card_countries)."""

    id = "custom"
    residence_country = ""
    instruments_held: tuple[str, ...] = ()
    obtain_remote_instruments = True

    def __init__(self, answers: Mapping[Question, str]) -> None:
        self._answers = answers

    def _ask(self, question: Question, answers: tuple[str, ...] = _BOOL) -> str:
        if question not in self._answers:
            raise _Ask(question, answers)
        return self._answers[question]

    def _yes(self, *question: str) -> bool:
        return self._ask(tuple(question)) == "true"

    def lives_in(self, cc: str) -> bool:
        return self._yes("lives", cc)

    def lives_in_eea(self, eea: frozenset[str]) -> bool:
        return self._yes("lives_eea")

    def has_document(self, cc: str) -> bool:
        return self._yes("doc", cc)

    def has_any_document(self) -> bool:
        return self._yes("doc_any")

    def has_eea_document(self, eea: frozenset[str]) -> bool:
        return self._yes("doc_eea")

    def has_residence_document(self) -> bool:
        return self._yes("doc_residence")

    def has_phone(self, cc: str) -> bool:
        return self._yes("phone", cc)

    def has_address(self, cc: str) -> bool:
        return self._yes("address", cc)

    def holds_card_of(self, cc: str) -> bool:
        return self._yes("card", cc)

    def presence_in(self, cc: str) -> str | None:
        value = self._ask(("presence", cc), _PRESENCE)
        return None if value == "none" else value

    def curated_record(
        self, feasibility: FeasibilityFile, service_id: str, target: str
    ) -> FeasibilityRecord | None:
        for record in feasibility.records:
            if (
                record.service_id == service_id
                and record.target_country == target
                and self._yes("preset", record.profile)
            ):
                return record
        return None

    def bundle_record(self, records: Iterable[BundleVerdictRecord]) -> BundleVerdictRecord | None:
        for record in records:
            if self._yes("preset", record.profile):
                return record
        return None


def explore(run: Callable[[SymbolicProfile], Any], leaf: Callable[[Any], Any]) -> Tree:
    """The decision tree of `run` over the profile questions it asks, leaves `leaf(result)`.
    Subtrees whose answers all lead to the same thing are collapsed into it."""
    runs = 0

    def go(answers: dict[Question, str]) -> Tree:
        nonlocal runs
        runs += 1
        if runs > MAX_RUNS:
            raise RuntimeError("profile rules: too many evaluator runs for one pair")
        try:
            result = run(SymbolicProfile(answers))
        except _Ask as ask:
            branches = {a: go({**answers, ask.question: a}) for a in ask.answers}
            first = next(iter(branches.values()))
            if all(b == first for b in branches.values()):
                return first
            return {"ask": list(ask.question), "answers": branches}
        return {"verdict": leaf(result)}

    return go({})


# ------------------------------------------------------------------- concrete profiles


def matches(profile: Mapping[str, Any], preset: Mapping[str, Any]) -> bool:
    """Whether two capability dicts (Profile.capabilities) describe the same profile."""
    return (
        profile["residence_country"] == preset["residence_country"]
        and all(set(profile[k]) == set(preset[k]) for k in _CAPABILITY_LISTS)
        and dict(profile["presence"]) == dict(preset["presence"])
    )


def answer(
    question: list[str] | Question,
    profile: Mapping[str, Any],
    *,
    eea: frozenset[str],
    presets: Mapping[str, Mapping[str, Any]],
) -> str:
    """A capability dict's answer to one question (the browser's twin is verdicts.ts)."""
    kind, *args = question
    cc = args[0] if args else ""
    r = profile["residence_country"]
    docs = set(profile["id_document_countries"])
    value: bool
    if kind == "presence":
        return profile["presence"].get(cc) or "none"
    if kind == "lives":
        value = r == cc
    elif kind == "lives_eea":
        value = r in eea
    elif kind == "doc":
        value = cc in docs
    elif kind == "doc_any":
        value = bool(docs)
    elif kind == "doc_eea":
        value = bool(docs & eea)
    elif kind == "doc_residence":
        value = r in docs
    elif kind == "phone":
        value = cc in profile["phone_countries"]
    elif kind == "address":
        value = cc == r or cc in profile["address_countries"]
    elif kind == "card":
        value = cc in profile["card_countries"]
    elif kind == "preset":
        value = cc in presets and matches(profile, presets[cc])
    else:
        raise ValueError(f"unknown profile question {question!r}")
    return "true" if value else "false"


def evaluate_tree(
    tree: Tree,
    profile: Mapping[str, Any],
    *,
    eea: frozenset[str],
    presets: Mapping[str, Mapping[str, Any]],
) -> Any:
    while "verdict" not in tree:
        tree = tree["answers"][answer(tree["ask"], profile, eea=eea, presets=presets)]
    return tree["verdict"]

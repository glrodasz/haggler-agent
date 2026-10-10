"""The research subject kinds: one registry entry per kind. CONTRACT.

A subject is what one research agent run looks at, named by a key "<kind>:<id>[:<id>...]":

    service      service:<service_id>               service:netflix
    pair         pair:<service_id>:<country>        pair:netflix:CO
    instrument   instrument:<instrument_id>         instrument:revolut_eea
    bundle       bundle:<bundle_id>                 bundle:meli_plus_co_total

Keys are built and read leniently, as they always were: the builder joins the ids without
checking them and the readers split stored keys without rejecting malformed ones ("pair:netflix:",
"service:a:b"), so a bad row in data/research degrades a lookup instead of crashing site-data or
the review CLI. Only the number of ids a builder is given is checked (a call-site bug).

Everything that differs per kind and is not prose is declared here, once: the key layout, the
facts file (research/latest/ and data/research/) and whether it is optional, the freshness
window, the agent's name, the vocabulary level (FieldSpec.level), the position in a run and which
pair verdicts the kind's facts can change. Derived from it: fields.FIELDS_BY_LEVEL,
io.KIND_FILES / LATEST_FILES / DATA_FILES / OPTIONAL_DATA_FILES, plan._WINDOWS,
agents.AGENT_NAMES, nodes.KIND_ORDER, review.select_claims and the review CLI's affected pairs.

An optional kind's file (bundle: bundles.json) is written to research/latest/ only when the kind
has subjects, and `haggler sync` copies it only when the run's manifest lists it, so a run.json
written before the kind existed is still a valid research run. The bundle kind runs last: when a
run's call or search-query budget runs out, its subjects are the first ones skipped. Its window
is the default; haggler.bundles.plan shortens it for a bundle whose offer ends soon or whose
pages changed.

Adding a kind = its name in SubjectKind and one KindSpec in _SPECS, plus its vocabulary
(fields.FIELDS with level = the kind) and its prompt (agents.INSTRUCTIONS); the module checks
itself at import and tests/unit/test_research_kinds.py checks the rest.

Pure: imports nothing from haggler (models.research takes SubjectKind from here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

SubjectKind = Literal["service", "pair", "instrument", "bundle"]
# Which researched pairs' verdicts a subject's facts can change: "self" (the pair the subject
# is), "service" (every pair of the subject's service), "all" (every pair), "none".
PairScope = Literal["self", "service", "all", "none"]
SEP = ":"


@dataclass(frozen=True)
class SubjectRef:
    """A subject key split into its kind and ids. The ids are not checked: a malformed stored key
    keeps whatever it splits into (missing, extra or empty ids)."""

    kind: SubjectKind
    ids: tuple[str, ...]

    @property
    def key(self) -> str:
        return SEP.join((self.kind, *self.ids))

    @property
    def owner(self) -> str | None:
        """The first id: what a bare CLI selector names ("netflix" = service:netflix and every
        pair:netflix:*); None when the key has no ids."""
        return self.ids[0] if self.ids else None


@dataclass(frozen=True)
class KindSpec:
    name: SubjectKind
    # Names of the ids after the prefix, in key order (first = owner). They are also the
    # attribute names of plan.PlannedSubject.
    parts: tuple[str, ...]
    # File of the kind's SubjectFacts under research/latest/ and data/research/.
    data_file: str
    # Days after the last successful research before the subject is due again.
    window_days: int
    agent_name: str
    # Position in a run: the research node runs the kinds in this order, so the earlier ones
    # get the call and search-query budget first.
    run_order: int
    pair_scope: PairScope
    # The data file is optional (see the module docstring): written and synced only when there
    # is something in it.
    optional: bool = False

    @property
    def prefix(self) -> str:
        return self.name + SEP

    @property
    def field_level(self) -> SubjectKind:
        """FieldSpec.level of the fields this kind may report (the kind itself)."""
        return self.name

    def key(self, *ids: str) -> str:
        """The ids joined under the prefix, not checked (an empty id or one holding SEP makes a
        key that does not parse back); ValueError only for the wrong number of ids."""
        if len(ids) != len(self.parts):
            raise ValueError(f"a {self.name} key needs {', '.join(self.parts)}; got {ids!r}")
        return SEP.join((self.name, *ids))

    def split(self, key: str) -> SubjectRef:
        """The key of a subject known to be of this kind (a SubjectFacts' own kind) split at SEP
        after its prefix, whatever the prefix and however many ids it has."""
        return SubjectRef(self.name, tuple(key.split(SEP)[1:]))

    def parse(self, key: str) -> SubjectRef | None:
        """The key's ids when it has this kind's prefix and number of ids (empty ids included),
        else None."""
        if not key.startswith(self.prefix):
            return None
        ref = self.split(key)
        return ref if len(ref.ids) == len(self.parts) else None


_SPECS: tuple[KindSpec, ...] = (
    KindSpec(
        name="service",
        parts=("service_id",),
        data_file="services.json",
        window_days=90,
        agent_name="service_researcher",
        run_order=1,
        pair_scope="service",
    ),
    KindSpec(
        name="pair",
        parts=("service_id", "country"),
        data_file="pairs.json",
        window_days=60,
        agent_name="pair_researcher",
        run_order=0,
        pair_scope="self",
    ),
    KindSpec(
        name="instrument",
        parts=("instrument_id",),
        data_file="instruments.json",
        window_days=120,
        agent_name="instrument_researcher",
        run_order=2,
        pair_scope="all",
    ),
    KindSpec(
        name="bundle",
        parts=("bundle_id",),
        data_file="bundles.json",
        window_days=28,
        agent_name="bundle_researcher",
        # Last: the Phase 2 subjects never wait for the budget a bundle spent.
        run_order=3,
        # A bundle's verdicts are its own (haggler.bundles.evaluate), never a pair's.
        pair_scope="none",
        optional=True,
    ),
)


def _check(specs: tuple[KindSpec, ...]) -> None:
    names = tuple(s.name for s in specs)
    if names != get_args(SubjectKind):
        raise ValueError(f"kinds {names} do not match SubjectKind {get_args(SubjectKind)}")
    for attr in ("data_file", "agent_name", "run_order"):
        values = [getattr(s, attr) for s in specs]
        if len(set(values)) != len(values):
            raise ValueError(f"kinds share a {attr}: {values}")
    for s in specs:
        if not s.parts or s.window_days <= 0 or not s.data_file.endswith(".json"):
            raise ValueError(f"kind {s.name}: needs id parts, a window and a .json file")
        if s.data_file == "state.json":
            raise ValueError(f"kind {s.name}: state.json is the research state")
        # The review CLI reads the pair off a "self" key and the service off a "service" key.
        if s.pair_scope == "self" and s.parts != ("service_id", "country"):
            raise ValueError(f"kind {s.name}: pair_scope self needs parts (service_id, country)")
        if s.pair_scope == "service" and s.parts[0] != "service_id":
            raise ValueError(f"kind {s.name}: pair_scope service needs service_id first")


_check(_SPECS)

# Declaration order = file order (research/latest/*, data/research/*, run.json manifests).
KINDS: dict[SubjectKind, KindSpec] = {s.name: s for s in _SPECS}
RUN_ORDER: tuple[SubjectKind, ...] = tuple(
    s.name for s in sorted(_SPECS, key=lambda s: s.run_order)
)


def _spec_of(key: str) -> KindSpec | None:
    head = key.split(SEP, 1)[0]
    return next((s for s in _SPECS if s.name == head), None)


def kind_of(key: str) -> SubjectKind:
    """The kind named by a key's prefix ("pair:netflix:CO" -> "pair"); ValueError if unknown."""
    spec = _spec_of(key)
    if spec is None:
        raise ValueError(f"unknown subject kind in key {key!r}")
    return spec.name


def subject_key(kind: SubjectKind, *ids: str) -> str:
    """ "service:netflix", "pair:netflix:CO", "instrument:revolut_eea",
    "bundle:meli_plus_co_total"."""
    return KINDS[kind].key(*ids)


def parse_key(key: str) -> SubjectRef | None:
    """The kind and ids of a key, or None when its prefix is not a known kind or it has the
    wrong number of ids (KindSpec.parse)."""
    spec = _spec_of(key)
    return spec.parse(key) if spec is not None else None

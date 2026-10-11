"""Review state of research claims (data/curated/research_review.yaml).

A decision holds for one claim id AND its evidence hash. A claim whose hash has no decision is
pending; the last decision in the file wins.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from haggler.models.research import Claim, ReviewDecision, ReviewFile, SubjectFacts
from haggler.research.fields import FIELD_SPECS, value_label_es

ClaimReview = Literal["accepted", "rejected", "pending"]
STATUS_ES = {"accepted": "aceptada", "rejected": "rechazada", "pending": "pendiente"}
CLASS_ES = {"official": "oficial", "press": "prensa", "forum": "foro"}


class ReviewIndex:
    def __init__(self, review: ReviewFile | None = None) -> None:
        self._latest: dict[tuple[str, str], ReviewDecision] = (
            review.latest() if review is not None else {}
        )

    def decision(self, claim: Claim) -> ReviewDecision | None:
        return self.lookup(claim.id, claim.hash)

    def lookup(self, id_: str, hash_: str) -> ReviewDecision | None:
        """The last decision on (id, hash): a claim's, or a bundle proposal's
        (haggler.bundles.proposals: a watcher hint has an id and a hash too)."""
        return self._latest.get((id_, hash_))

    def status(self, claim: Claim) -> ClaimReview:
        d = self.decision(claim)
        if d is None:
            return "pending"
        return "accepted" if d.decision == "accept" else "rejected"


def select_claims(
    subjects: Iterable[SubjectFacts], selector: str
) -> list[tuple[SubjectFacts, Claim]]:
    """Claims named by a CLI selector: a claim id, a subject key ("pair:netflix:CO"), or a bare
    service/instrument id, which names every subject it owns (the first id of the key, see
    research.kinds: "netflix" = its service subject and every pair of that service)."""
    out: list[tuple[SubjectFacts, Claim]] = []
    for subject in subjects:
        owner = subject.ref.owner
        whole = selector == subject.key or (owner is not None and selector == owner)
        for claim in subject.claims:
            if whole or claim.id == selector:
                out.append((subject, claim))
    return out


def claim_lines(claim: Claim, status: ClaimReview) -> list[str]:
    """A claim as the review CLI prints it: state, id and hash; field and value; source; quote."""
    spec = FIELD_SPECS.get(claim.field)
    field_es = spec.label_es if spec else claim.field
    when = f" ({claim.event_date.isoformat()})" if claim.event_date else ""
    retrieved = claim.check.retrieved_at.date().isoformat() if claim.check.retrieved_at else "—"
    return [
        f"  [{STATUS_ES[status]}] {claim.id}  hash={claim.hash}",
        f"      {field_es}: {value_label_es(claim.field, claim.value)}{when}",
        f"      fuente {CLASS_ES.get(claim.check.source_class, claim.check.source_class)}, "
        f"consultada el {retrieved}: {claim.url}",
        f"      “{claim.quote}”",
    ]

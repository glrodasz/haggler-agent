"""Review state of research claims (data/curated/research_review.yaml).

A decision holds for one claim id AND its evidence hash. A claim whose hash has no decision is
pending; the last decision in the file wins.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from haggler.models.research import Claim, ReviewDecision, ReviewFile, SubjectFacts

ClaimReview = Literal["accepted", "rejected", "pending"]


class ReviewIndex:
    def __init__(self, review: ReviewFile | None = None) -> None:
        self._latest: dict[tuple[str, str], ReviewDecision] = (
            review.latest() if review is not None else {}
        )

    def decision(self, claim: Claim) -> ReviewDecision | None:
        return self._latest.get((claim.id, claim.hash))

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

from __future__ import annotations

from haggler.coverage import Result, render


def test_render_counts_useful_ties_and_lists_the_best():
    report = {
        "SE": [
            Result(None, False, {2: 0.0, 3: 0.0}, ()),
            Result("CO", False, {2: 10.0, 3: 12.0}, ("spotify",)),
            Result("CO", True, {2: 20.0, 3: 23.4}, ("netflix", "spotify")),
            Result("AR", True, {2: 0.0, 3: 0.0}, ()),
        ]
    }
    md = render(["AR", "CO", "SE"], report, {"SE": "Suecia", "CO": "Colombia", "AR": "Argentina"})
    assert "Argentina, Colombia, Suecia." in md
    assert "| Suecia | 0 % / 0 % | 1 de 2 | Colombia 23 % |" in md

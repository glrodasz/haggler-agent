"""haggler.text: browser-like line rendering and the matching/hash helpers."""

from __future__ import annotations

import pytest
from selectolax.lexbor import LexborHTMLParser

from haggler import text
from haggler.text import (
    contains_normalized,
    html_to_lines,
    html_to_text,
    json_scripts,
    node_lines,
    normalize_for_match,
    page_title,
    price_windows,
    render_lines,
    text_hash,
    text_node_lines,
    walk_json,
)


def _body(html: str) -> str:
    return f"<html><head><title>T</title></head><body>{html}</body></html>"


@pytest.mark.parametrize(
    ("html", "lines"),
    [
        # Inline markup joins; block elements break.
        ("<p><b>Basic</b>: 129 kr<span>/mån</span></p>", ["Basic: 129 kr/mån"]),
        ("<p><b>Sweden</b> (SEK)</p>", ["Sweden (SEK)"]),
        ("<div><span>Premium</span><span>Duo</span></div>", ["Premium Duo"]),
        ("<div><p>Standard</p><p>169 kr</p></div>", ["Standard", "169 kr"]),
        (
            "<li><span>Disney+ Premium</span> <span>159,00&nbsp;kr</span></li>",
            ["Disney+ Premium 159,00 kr"],
        ),
        # <br> breaks a line.
        ("<p>50GB: 12 kr<br>200GB: 39 kr</p>", ["50GB: 12 kr", "200GB: 39 kr"]),
        # Short <sup> footnote marks are dropped: they never glue to numbers or names.
        ("<p>$59.99<sup>4</sup>/mo.</p>", ["$59.99/mo."]),
        ("<p>Bahamas<sup>2</sup> (USD)</p>", ["Bahamas (USD)"]),
        ("<p>Prova gratis<sup><a href='#f3'>3</a></sup></p>", ["Prova gratis"]),
        ("<h3>Student<sup class='footnote'>*</sup></h3>", ["Student"]),
        ("<p>Apple TV<sup>™</sup> 99 kr</p>", ["Apple TV 99 kr"]),
        # Longer <sup> text stays, as separate words.
        ("<p>Price<sup>incl. VAT</sup>129 kr</p>", ["Price incl. VAT 129 kr"]),
        # Invisible characters go, NBSP and runs of spaces collapse.
        ("<p>månads&shy;kostnad sträck​tittande</p>", ["månadskostnad sträcktittande"]),
        ("<p>1&nbsp;590,00 kr \n\t per   år</p>", ["1 590,00 kr per år"]),
        # Never rendered.
        (
            "<p>a</p><script>var x = '9 kr';</script><style>p{}</style><!-- 5 kr --><p>b</p>",
            ["a", "b"],
        ),
        ("<template><p>99 kr</p></template><noscript>JS</noscript><svg><text>1</text></svg>", []),
        # Real dump line shapes (apple.com/se, 2026-10-08).
        (
            "<p><s><span class='visuallyhidden'>Inte </span>339&nbsp;kr/mån"
            "<span class='visuallyhidden'> per månad</span></s></p>",
            ["Inte 339 kr/mån per månad"],
        ),
    ],
)
def test_html_to_lines(html, lines):
    assert html_to_lines(_body(html)) == lines


def test_title_and_head_are_not_body_text():
    assert html_to_lines("<html><head><title>Plans 129 kr</title></head><body>x</body></html>") == [
        "x"
    ]


def test_drop_chrome():
    html = _body(
        "<header><a>Premium Family</a></header><nav>Menu</nav>"
        "<main><p>Premium Duo 169 kr</p></main><footer>Premium Student</footer>"
    )
    assert html_to_lines(html) == [
        "Premium Family",
        "Menu",
        "Premium Duo 169 kr",
        "Premium Student",
    ]
    assert html_to_lines(html, drop_chrome=True) == ["Premium Duo 169 kr"]
    # A page that is all chrome keeps it rather than rendering nothing.
    only = _body("<header><p>Standard: 169 kr</p></header>")
    assert html_to_lines(only, drop_chrome=True) == ["Standard: 169 kr"]


def test_html_to_text_and_empty_input():
    assert html_to_text(_body("<p>a</p><p>b</p>")) == "a\nb"
    assert html_to_lines("") == []


def test_render_lines_heading_and_context():
    tree = LexborHTMLParser(
        _body(
            '<section class="plans" data-analytics-section-engagement="name:plans">'
            '<h3 id="t">Individual</h3><div class="price"><span>$19.95</span>/mo.</div></section>'
        )
    )
    lines = render_lines(tree.body, context=True)
    assert [(ln.text, ln.heading) for ln in lines] == [("Individual", 3), ("$19.95/mo.", 0)]
    assert (
        "plans" in lines[0].context and "name:plans" in lines[0].context and "t" in lines[0].context
    )
    assert "price" in lines[1].context
    # Without context=True the (costly) attribute trail is not collected.
    assert render_lines(tree.body)[1].context == ""
    assert render_lines(None) == []


def test_node_lines_on_a_fragment():
    cell = LexborHTMLParser("<table><tr><td>Croatia³ (Euro)</td><td>0.99&nbsp;€</td></tr></table>")
    tds = cell.css("td")
    assert [node_lines(td) for td in tds] == [["Croatia³ (Euro)"], ["0.99 €"]]
    assert node_lines(None) == []


def test_text_node_lines_one_line_per_text_node():
    html = _body("<li><span>Disney+ Premium</span><span>159,00 kr</span></li><script>x</script>")
    assert text_node_lines(html) == ["Disney+ Premium", "159,00 kr"]


def test_page_title():
    assert page_title("<html><head><title> Apple One – Apple (SE) </title></head></html>") == (
        "Apple One – Apple (SE)"
    )
    assert page_title("<p>no title</p>") == ""


def test_normalize_and_contains():
    assert normalize_for_match("Standard – 129 kr’s") == "standard - 129 kr's"
    assert normalize_for_match("A−B") == "a-b"
    assert contains_normalized("Premium Individual 129 kr/mån", "individual 129 kr")
    assert not contains_normalized("Premium Individual", "duo")


def test_text_hash_is_stable():
    # Pinned: stored hashes decide whether the LLM fallback reruns; a change re-triggers it.
    assert text_hash("Premium Individual 129 kr/mån") == "e27aeeb7c2184a35"
    assert text_hash("A B C−D ’ x") == "109234b06a9f047c"
    assert text_hash("Premium  Individual 129 kr/mån") == text_hash("premium individual 129 kr/mån")
    assert text.bytes_hash(b"x") == text.bytes_hash(b"x") != text.bytes_hash(b"y")


def test_price_windows():
    lines = [f"line {i}" for i in range(10)]
    lines[5] = "Standard 169 kr"
    out = price_windows(lines, ["SEK"], context=1)
    assert out.split("\n") == ["…", "line 4", "Standard 169 kr", "line 6"]
    assert price_windows(["no prices here"], ["SEK"]) == "no prices here"


def test_json_scripts_and_walk_json():
    html = (
        '<script type="application/ld+json">{"@type": "Offer", "price": "129"}</script>'
        '<script id="__NEXT_DATA__">{"props": {"plans": [{"name": "Duo"}]}}</script>'
        '<script type="application/json">not json</script><script>var x = 1;</script>'
    )
    docs = dict(json_scripts(html))
    assert docs["application/ld+json"] == {"@type": "Offer", "price": "129"}
    paths = [p for p, _, _ in walk_json(docs["__NEXT_DATA__"])]
    assert paths == ["props", "props.plans", "props.plans[0].name"]

"""Render a single-page, source-bound executive brief of the OpenAI OPDP report.

Run from the repository root after installing reportlab and pypdf:
    python scripts/build_openai_opdp_executive_brief.py
The source report JSON and PDF are published under docs/.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import Counter
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
STEM = "OpenAI_Mathematics_OPDP_Assessment_2026-10-07"
BRIEF = "OpenAI_Mathematics_OPDP_Executive_Brief_2026-10-08.pdf"
REPO = "https://github.com/alejandrozu/ulam-opdp-difficulty-atlas"
ANNOUNCEMENT = "https://openai.com/index/sharing-ai-progress-in-mathematics/"
INK = colors.HexColor("#142A36")
TEAL = colors.HexColor("#007A83")
MUTED = colors.HexColor("#4D616B")
LIGHT = colors.HexColor("#EDF5F5")


def font_setup(directory: Path) -> None:
    for name, filename in (("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf"),
                           ("Arial-Italic", "ariali.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(directory / filename)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold",
                                 italic="Arial-Italic", boldItalic="Arial-Bold")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, default=Path("C:/Windows/Fonts"))
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / BRIEF)
    args = parser.parse_args()
    font_setup(args.font_dir)
    source = ROOT / "docs" / f"{STEM}.json"
    report = ROOT / "docs" / f"{STEM}.pdf"
    document = json.loads(source.read_text(encoding="utf-8"))
    rows = document["records"]
    relations = Counter(r["archive_relation"] for r in rows)
    bases = Counter(r["difficulty_basis_label"] for r in rows)
    expected = {"same_target": 59, "partial_or_special_case": 91,
                "related_target": 287, "lexical_candidate": 147,
                "unresolved_no_match": 138}
    assert len(rows) == 722 and len({r["family_id"] for r in rows}) == 372
    assert len({r["discipline"] for r in rows}) == 17
    assert dict(relations) == expected
    assert document["atlas_records"] == 102819
    assert len(PdfReader(report).pages) == 254
    assert bases["Published original CFSD-1000 target difficulty"] == 26
    assert bases["Provisional OPDP / CFSD-1000 reconstruction (C0)"] == 692
    assert bases["Historical independent-proof burden estimate (C0)"] == 4
    assert all(r["formal_verification_performed"] is False for r in rows)

    left_sections = [
        ("THE AUTHOR'S CONTRIBUTION: OPDP",
         "<b>Alejandro Zarzuelo Urdiales created and authored the Open Problem Difficulty "
         "Profile (OPDP), with ChatGPT 5.6 Sol assistance.</b> It replaces an opaque difficulty "
         "label with explicit dimensions, public rationales, source identifiers and versioned "
         "assessments. This analysis applies his framework to OpenAI's mathematics release: "
         "every manuscript receives a mathematical description, a precisely stated scoring "
         "target, an archive relation, a justified difficulty assessment and a conditional "
         "OpenMath judging calculation. OPDP is the mechanism connecting a large catalogue "
         "of claims to inspectable research judgments."),
        ("WHY THIS ANALYSIS MATTERS",
         "<b>The unit of mathematical progress is a scoped result, not a paper count.</b> "
         "General theorems, conditional results, finite constructions, special cases and "
         "alternative proofs cannot be compared by volume alone. Hypotheses, quantifiers, "
         "conclusions and computational models determine whether two claims address the "
         "same target. OPDP makes these distinctions explicit before assigning credit. "
         "This supports expert-review prioritization, transparent competition judging and "
         "an auditable account of AI-assisted mathematical research for technical readers "
         "and scientific publishers."),
        ("A MULTIDIMENSIONAL MECHANISM",
         "Nine normalized burdens feed the 0-1000 full-solution difficulty <b>D</b>: "
         "AI-relative reasoning (weight 400), intrinsic mathematical difficulty (170), "
         "human resistance (90), inverse tractability (150), verification (45), "
         "formalization (35), prerequisites (40), context/ambiguity (40) and tool "
         "constraints (30). Discovery, checking and formal encoding are therefore "
         "separate obstacles. For weighted burden <b>R</b>, the published transformation "
         "is <b>D = 1000[1 - (1 - R/1000)<super>1.4</super>]</b>; new estimates are rounded "
         "to 25. The fixed target protocol is ChatGPT 5.6 Sol Ultra, public literature "
         "and computational/proof tools, with a nominal 100-agent-hour ceiling. Unknown "
         "human attention is neutral (0.5), not invented historical effort."),
        ("FROM PROFILE TO JUDGING",
         "Under the OpenMath non-focus scenario, <b>F = m p D<super>2</super>/1000</b>: "
         "<b>m</b> distinguishes an original target from an admitted variation; <b>p</b> "
         "describes progress toward the explicitly named target. Completing a narrow "
         "result is not automatically completing its broader parent. This is a judging "
         "policy, not a measured law of scientific value. Manuscript-level scenarios "
         "must not be added as independent awards: companions, consequences and "
         "alternative proofs require family-level accounting and novelty review."),
    ]
    right_sections = [
        ("WHAT THE ANALYSIS ESTABLISHES",
         "All <b>722</b> catalogue manuscripts were acquired and individually documented "
         "in a <b>254-page</b> report, across <b>372</b> upstream result families and "
         "<b>17</b> disciplines. Comparison with the frozen <b>102,819-record</b> OPDP "
         "atlas yields <b>59 same-target scope matches (8.2%)</b>, <b>91 partial or "
         "special-case relations (12.6%)</b>, <b>287 related targets (39.8%)</b>, "
         "<b>147 lexical candidates (20.4%)</b> and <b>138 unresolved matches (19.1%)</b>. "
         "These are manuscript-level scope assessments, not verified resolutions or "
         "mutually distinct mathematical accomplishments."),
        ("CONCRETE SAFEGUARDS AGAINST OVERCLAIMING",
         "The reported asymptotic matrix-multiplication bound "
         "<b>omega &lt;= 9/4</b> is a different target from a fixed 3 x 3 scheme using "
         "23 products; the latter's registered difficulty is not transferred. Likewise, "
         "constant-error synthesis with a target-dependent Boolean oracle is not "
         "equivalent to exact gate synthesis in a different computational model. "
         "The report preserves <b>26</b> applicable original scores, supplies <b>692</b> "
         "new lowest-confidence (C0) estimates, and assigns no new-resolution credit to "
         "<b>four</b> acknowledged reconstructions of known main results. Alternative "
         "proofs may still have scientific value; they are not new resolutions of "
         "the already-settled target."),
        ("VALUE FOR AI RESEARCH AND SELF-IMPROVEMENT",
         "A model may search effectively yet fail at long dependencies, literature "
         "integration, independent verification or formalization. Separating these "
         "barriers can inform problem selection, tool development and allocation of "
         "expert review. For recursive self-improvement (RSI), OPDP offers a way to "
         "specify which bottleneck an intervention is intended to improve; this "
         "analysis does <b>not</b> demonstrate RSI. A prospective test would freeze "
         "targets and budgets, rate difficulty before seeing outcomes, hold out source "
         "families, independently check results and compare success/cost by dimension. "
         "Source-stratified evaluation is essential to avoid confusing a change in "
         "problem mix with a change in capability."),
        ("EVIDENCE BOUNDARY AND REUSE",
         "These are retrospective, source-based editorial judgments, not a calibrated "
         "AI success probability or proof certification. No solve campaign or Lean "
         "kernel replay was performed. The atlas includes excerpt-derived C0 records, "
         "not 102,819 independently verified distinct open conjectures. Unresolved "
         "matching proves neither novelty nor absence. Official new-target admission "
         "requires three independent difficulty assessments plus scope, novelty and "
         "proof review. The public JSON retains per-target rationale, scope, source "
         "hashes, component inputs and calculation traces. The analysis is published "
         "separately and does not silently modify canonical atlas records."),
    ]

    width, height = A4
    margin, gutter = 32, 21
    column_width = (width - 2 * margin - gutter) / 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(args.output), pagesize=A4, pageCompression=1)
    c.setTitle("OPDP: an auditable framework for assessing AI-generated mathematics")
    c.setAuthor("Alejandro Zarzuelo Urdiales")
    c.setSubject("One-page executive brief of the 722-manuscript OPDP analysis")
    c.setCreator("OPDP | ChatGPT-assisted preparation")
    c.setFillColor(TEAL)
    c.rect(0, height - 7, width, 7, stroke=0, fill=1)

    styles = {
        "title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=23,
                                leading=25, textColor=INK),
        "subtitle": ParagraphStyle("subtitle", fontName="Arial", fontSize=10.3,
                                   leading=13.5, textColor=TEAL),
        "body": ParagraphStyle("body", fontName="Arial", fontSize=9.0,
                               leading=11.2, textColor=INK),
        "heading": ParagraphStyle("heading", fontName="Arial-Bold", fontSize=8.4,
                                  leading=10.5, textColor=TEAL),
        "footer": ParagraphStyle("footer", fontName="Arial", fontSize=8,
                                 leading=10.5, textColor=MUTED),
    }
    boxes = []
    def paragraph(text: str, x: float, y: float, w: float, kind: str) -> float:
        p = Paragraph(text, styles[kind])
        _, h = p.wrap(w, height)
        p.drawOn(c, x, y - h)
        boxes.append({"kind": kind, "x": x, "top": y, "bottom": y - h,
                      "width": w, "height": h})
        return y - h

    y = height - 32
    y = paragraph("Making AI-generated mathematics<br/>auditable with OPDP", margin,
                  y, width - 2 * margin, "title") - 7
    y = paragraph("<b>Alejandro Zarzuelo Urdiales</b> | Creator and author of OPDP<br/>"
                  "Executive research brief | AI-assisted preparation | 8 October 2026",
                  margin, y, width - 2 * margin, "subtitle") - 13
    strip_h = 45
    c.setFillColor(LIGHT)
    c.roundRect(margin, y - strip_h, width - 2 * margin, strip_h, 5, stroke=0, fill=1)
    for index, (value, label) in enumerate((("722", "manuscripts assessed"),
                                          ("372", "result families"),
                                          ("102,819", "archived problem records"),
                                          ("254", "pages of analysis"))):
        x = margin + 10 + index * (width - 2 * margin) / 4
        c.setFillColor(TEAL)
        c.setFont("Arial-Bold", 20)
        c.drawString(x, y - 23, value)
        c.setFillColor(MUTED)
        c.setFont("Arial", 7.0)
        c.drawString(x, y - 36, label)
    y -= strip_h + 15
    column_top = y
    column_bottoms = []
    for index, sections in enumerate((left_sections, right_sections)):
        x = margin + index * (column_width + gutter)
        top = column_top
        for heading, text in sections:
            top = paragraph(heading, x, top, column_width, "heading") - 4
            top = paragraph(text, x, top, column_width, "body") - 8
        column_bottoms.append(top + 8)
    c.setStrokeColor(colors.HexColor("#C7D8DD"))
    c.setLineWidth(0.45)
    c.line(width / 2, column_top, width / 2, min(column_bottoms))

    footer_top = min(column_bottoms) - 12
    c.setStrokeColor(TEAL)
    c.setLineWidth(0.75)
    c.line(margin, footer_top + 4, width - margin, footer_top + 4)
    report_url = REPO + "/blob/main/docs/" + STEM + ".pdf"
    json_url = REPO + "/blob/main/docs/" + STEM + ".json"
    footer_top = paragraph(
        '<b>Read the complete evidence:</b> <link href="' + report_url +
        '" color="#007A83">Full analysis (254-page PDF)</link> | '
        '<link href="' + json_url + '" color="#007A83">Scoring JSON</link> | '
        '<link href="' + REPO + '" color="#007A83">OPDP repository</link>',
        margin, footer_top, width - 2 * margin, "footer") - 3
    footer_top = paragraph('<link href="' + REPO + '" color="#007A83">' + REPO +
                           '</link>', margin, footer_top, width - 2 * margin, "footer") - 3
    footer_top = paragraph(
        'Source: <link href="' + ANNOUNCEMENT +
        '" color="#007A83">OpenAI release, 6 October 2026</link>; openai/math '
        'snapshot adc7f1241b42; OPDP v1.8 snapshot e0e18186a142. '
        'Full hashes and individual justifications are in the linked report/JSON.',
        margin, footer_top, width - 2 * margin, "footer")
    if footer_top < 25:
        raise ValueError(f"One-page content overflow: footer bottom {footer_top:.2f}")
    assert all(b["x"] >= margin and b["x"] + b["width"] <= width - margin + 0.01
               and b["bottom"] >= 25 for b in boxes)
    c.save()
    reader = PdfReader(args.output)
    assert len(reader.pages) == 1
    links = [a.get_object().get("/A", {}).get("/URI") for a in reader.pages[0].get("/Annots", [])]
    assert report_url in links and json_url in links and REPO in links
    validation = {
        "schema": "opdp.openai-executive-brief-validation.v1",
        "date": "2026-10-08", "pages": 1, "all_text_boxes_inside_page": True,
        "body_font_pt": styles["body"].fontSize,
        "column_bottoms": column_bottoms, "footer_bottom": footer_top,
        "full_report_pages": 254, "manuscripts": 722, "families": 372,
        "atlas_records": 102819, "archive_relations": expected,
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "json_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "brief_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "links": links, "layout_boxes": boxes,
    }
    evidence = args.output.with_suffix(".validation.json")
    evidence.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "pages": 1,
                      "body_font_pt": styles["body"].fontSize, "footer_bottom": footer_top,
                      "text_word_count": len(reader.pages[0].extract_text().split())}))


if __name__ == "__main__":
    main()

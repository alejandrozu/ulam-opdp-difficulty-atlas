"""Build a polished, accessible one-page overview from the published OPDP evidence.

Run from the repository root:
    python scripts/build_openai_opdp_overview.py
Requires reportlab and pypdf. Arial fonts default to C:/Windows/Fonts.
"""
from __future__ import annotations

import argparse
import hashlib
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
REPO = "https://github.com/alejandrozu/ulam-opdp-difficulty-atlas"
FULL = "OpenAI_Mathematics_OPDP_Assessment_2026-10-07"
TECHNICAL = "OpenAI_Mathematics_OPDP_Executive_Brief_2026-10-08.pdf"
OVERVIEW = "OpenAI_Mathematics_OPDP_Overview_2026-10-09.pdf"
INK = colors.HexColor("#142A36")
TEAL = colors.HexColor("#007A83")
MUTED = colors.HexColor("#4D616B")
LIGHT = colors.HexColor("#EDF5F5")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, default=Path("C:/Windows/Fonts"))
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / OVERVIEW)
    args = parser.parse_args()
    for name, filename in (("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(args.font_dir / filename)))
    pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold",
                                 italic="Arial", boldItalic="Arial-Bold")
    data_path = ROOT / "docs" / f"{FULL}.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    rows = data["records"]
    assert len(rows) == 722 and len({r["family_id"] for r in rows}) == 372
    assert len({r["discipline"] for r in rows}) == 17
    assert data["atlas_records"] == 102819
    assert Counter(r["archive_relation"] for r in rows)["same_target"] == 59
    assert len(PdfReader(ROOT / "docs" / f"{FULL}.pdf").pages) == 254

    lead = (
        "Hundreds of AI-generated mathematics papers raise a fundamental question: "
        "<b>what counts as genuine progress?</b> A paper count cannot distinguish a "
        "general theorem from a special case, an alternative proof or a claim that "
        "still needs checking."
    )
    sections = [
        ("Difficulty has more than one dimension",
         "<b>OPDP, the Open Problem Difficulty Profile, was created and authored by "
         "Alejandro Zarzuelo Urdiales.</b> Developed with ChatGPT 5.6 Sol assistance, "
         "and further refined with newer and diverse models to capture the "
         "multidimensional complexity of mathematics, it separates the mathematical "
         "challenge from the knowledge needed, prospects for partial progress, "
         "difficulty for AI systems, and the work of checking and formally encoding "
         "a result. Public explanations make the reasons "
         "behind each assessment visible, rather than hiding them inside a single label."),
        ("From a catalogue to an evidence trail",
         "The analysis applies OPDP to <b>722 manuscripts</b> from OpenAI's mathematics "
         "release, representing <b>372 result families across 17 disciplines</b>. It "
         "compares their stated targets with <b>102,819 archived problem records</b> "
         "and identifies <b>59 same-target scope matches</b> - connections to archived "
         "questions, not independently verified resolutions. Every manuscript receives "
         "a description, an archive comparison, a justified difficulty assessment and "
         "a conditional competition-scoring calculation. A restricted result is "
         "distinguished from its broader conjecture; companion papers do not "
         "automatically receive separate awards."),
        ("Why this changes the conversation",
         "<b>The contribution is an inspectable framework for judging research claims "
         "at scale.</b> It helps researchers select targets, focus scarce expert "
         "attention on proof review and formalization, and compare different "
         "mathematical claims on explicit terms. Separating the difficulty of discovery from "
         "the difficulty of checking a result makes different research bottlenecks "
         "visible. The accompanying open data lets others inspect and revise the "
         "judgments as better evidence becomes available."),
    ]
    width, height = A4
    margin = 40
    content_width = width - 2 * margin
    styles = {
        "title": ParagraphStyle("title", fontName="Arial-Bold", fontSize=27,
                                leading=29.5, textColor=INK),
        "deck": ParagraphStyle("deck", fontName="Arial", fontSize=12,
                               leading=16, textColor=TEAL),
        "byline": ParagraphStyle("byline", fontName="Arial", fontSize=9.3,
                                 leading=12, textColor=MUTED),
        "lead": ParagraphStyle("lead", fontName="Arial", fontSize=11.5,
                               leading=15.7, textColor=INK),
        "body": ParagraphStyle("body", fontName="Arial", fontSize=11.8,
                               leading=16.0, textColor=INK),
        "heading": ParagraphStyle("heading", fontName="Arial-Bold", fontSize=11.3,
                                  leading=14, textColor=TEAL),
        "footer": ParagraphStyle("footer", fontName="Arial", fontSize=8.6,
                                 leading=11.8, textColor=MUTED),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(args.output), pagesize=A4, pageCompression=1)
    c.setTitle("Beyond paper counts: understanding AI mathematics")
    c.setAuthor("Alejandro Zarzuelo Urdiales")
    c.setSubject("OPDP, mathematical research assessment and the 722-manuscript analysis")
    c.setCreator("OPDP")
    c.setFillColor(TEAL)
    c.rect(0, height - 7, width, 7, stroke=0, fill=1)
    boxes = []
    def paragraph(text: str, x: float, top: float, w: float, kind: str) -> float:
        p = Paragraph(text, styles[kind])
        _, h = p.wrap(w, height)
        p.drawOn(c, x, top - h)
        boxes.append({"kind": kind, "x": x, "top": top, "bottom": top - h,
                      "width": w, "height": h})
        return top - h

    c.setFillColor(TEAL)
    c.setFont("Arial-Bold", 8.7)
    c.drawString(margin, height - 32, "OPDP  /  MATHEMATICAL RESEARCH")
    y = paragraph("Beyond paper counts:<br/>understanding AI mathematics",
                  margin, height - 48, content_width, "title") - 9
    y = paragraph("A clearer account of what is difficult, what is new<br/>"
                  "and what still needs to be checked.",
                  margin, y, content_width, "deck") - 9
    y = paragraph("<b>Alejandro Zarzuelo Urdiales</b> | Creator and author of OPDP | "
                  "9 October 2026", margin, y, content_width, "byline") - 16
    band_height = 49
    c.setFillColor(LIGHT)
    c.roundRect(margin, y - band_height, content_width, band_height, 5, stroke=0, fill=1)
    for i, (value, label) in enumerate((("722", "manuscripts assessed"),
                                       ("372", "result families"),
                                       ("17", "mathematical disciplines"))):
        x = margin + 15 + i * content_width / 3
        c.setFillColor(TEAL)
        c.setFont("Arial-Bold", 24)
        c.drawString(x, y - 26, value)
        c.setFillColor(MUTED)
        c.setFont("Arial", 8.6)
        c.drawString(x, y - 40, label)
    y -= band_height + 17
    y = paragraph(lead, margin, y, content_width, "lead") - 16
    for heading, text in sections:
        y = paragraph(heading, margin, y, content_width, "heading") - 5
        y = paragraph(text, margin, y, content_width, "body") - 13

    y = paragraph("<b>Evidence boundary:</b> Difficulty assessments remain provisional. "
                  "The report does not independently verify the proofs.",
                  margin, y, content_width, "footer") - 14
    c.setStrokeColor(TEAL)
    c.setLineWidth(0.7)
    c.line(margin, y + 4, width - margin, y + 4)
    technical_url = REPO + "/blob/main/docs/" + TECHNICAL
    full_url = REPO + "/blob/main/docs/" + FULL + ".pdf"
    y = paragraph(
        '<link href="' + technical_url + '" color="#007A83"><b>Technical overview</b></link>'
        '  |  <link href="' + full_url + '" color="#007A83"><b>Full analysis (254 pages)</b></link>'
        '  |  <link href="' + REPO + '" color="#007A83"><b>Open data and methodology</b></link>',
        margin, y, content_width, "footer") - 4
    y = paragraph('<link href="' + REPO + '" color="#007A83">' + REPO + '</link>',
                  margin, y, content_width, "footer")
    if y < 27:
        raise ValueError(f"One-page overflow; bottom is {y:.1f} pt")
    assert all(b["bottom"] >= 27 for b in boxes)
    c.save()
    reader = PdfReader(args.output)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "for the press" not in text.lower()
    assert "AI-assisted preparation" not in text
    assert "ChatGPT 5.6 Sol Ultra" not in text
    links = [a.get_object().get("/A", {}).get("/URI") for a in reader.pages[0].get("/Annots", [])]
    assert technical_url in links and full_url in links and REPO in links
    validation = {
        "schema": "opdp.openai-accessible-overview-validation.v1",
        "date": "2026-10-09", "pages": 1,
        "words": len(text.split()), "body_font_pt": styles["body"].fontSize,
        "all_text_boxes_inside_page": True, "footer_bottom": y,
        "technical_and_full_analysis_links_present": True,
        "overview_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "analysis_json_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "links": links, "layout_boxes": boxes,
    }
    args.output.with_suffix(".validation.json").write_text(
        json.dumps(validation, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in validation.items() if k not in {"links", "layout_boxes"}}))


if __name__ == "__main__":
    main()

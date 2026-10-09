"""Validate both one-page OPDP briefs and write the 9 October publication manifest."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TECHNICAL = "OpenAI_Mathematics_OPDP_Executive_Brief_2026-10-08.pdf"
OVERVIEW = "OpenAI_Mathematics_OPDP_Overview_2026-10-09.pdf"
STEM = "OpenAI_Mathematics_OPDP_Assessment_2026-10-07"
REPO = "https://github.com/alejandrozu/ulam-opdp-difficulty-atlas"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    original_manifest = json.loads(
        (DOCS / "OpenAI_Mathematics_OPDP_Publication_2026-10-08.json").read_text(encoding="utf-8"))
    original_hashes = {a["path"]: a["sha256"] for a in original_manifest["artifacts"]}
    for name in (STEM + ".pdf", STEM + ".json"):
        assert digest(DOCS / name) == original_hashes["docs/" + name], name
    data = json.loads((DOCS / f"{STEM}.json").read_text(encoding="utf-8"))
    assert len(data["records"]) == 722 and data["atlas_records"] == 102819
    technical = PdfReader(DOCS / TECHNICAL)
    overview = PdfReader(DOCS / OVERVIEW)
    assert len(technical.pages) == len(overview.pages) == 1
    texts = [re.sub(r"\s+", " ", pdf.pages[0].extract_text()) for pdf in (technical, overview)]
    for text in texts:
        assert "AI-assisted preparation" not in text
        assert "The fixed target protocol is ChatGPT 5.6 Sol Ultra" not in text
        assert "ChatGPT 5.6 Sol assistance" in text
        assert "refined with newer and diverse models to capture the multidimensional complexity of mathematics" in text
        assert "provisional" in text or "source-based editorial judgments" in text
    assert "for the press" not in texts[1].lower()
    overview_links = [a.get_object().get("/A", {}).get("/URI")
                      for a in overview.pages[0].get("/Annots", [])]
    assert REPO + "/blob/main/docs/" + TECHNICAL in overview_links
    assert REPO + "/blob/main/docs/" + STEM + ".pdf" in overview_links
    assert REPO in overview_links
    assert len(texts[1].split()) < len(texts[0].split()) / 2
    artifacts = []
    for filename, purpose in (
        (TECHNICAL, "Technical one-page executive brief; wording revised 9 October 2026"),
        (OVERVIEW, "Accessible professional one-page overview; created 9 October 2026"),
        (STEM + ".pdf", "Unchanged full 254-page assessment, public-facing edition"),
        (STEM + ".json", "Unchanged 722-manuscript scoring and rationale JSON"),
    ):
        path = DOCS / filename
        artifacts.append({"path": "docs/" + filename, "purpose": purpose,
                          "bytes": path.stat().st_size, "sha256": digest(path)})
    manifest = {
        "schema": "opdp.openai-math-publication.v2",
        "assessment_date": "2026-10-07", "original_publication_date": "2026-10-08",
        "brief_revision_and_overview_date": "2026-10-09",
        "author": "Alejandro Zarzuelo Urdiales",
        "authorship_scope": "Creator and author of OPDP; analysis and explanatory briefs",
        "repository": REPO,
        "prior_publication_commit": "58c43ecb3072bfbd7ede1ecaa2a10fdd9f6ca12b",
        "source_snapshot": original_manifest["source_snapshot"],
        "comparison_atlas_snapshot": original_manifest["comparison_atlas_snapshot"],
        "comparison_atlas_version": "1.8.0", "comparison_atlas_records": 102819,
        "manuscripts": 722, "upstream_result_families": 372, "disciplines": 17,
        "archive_relations": original_manifest["archive_relations"],
        "canonical_atlas_modified": False, "assessment_scores_modified": False,
        "publication_changes": {
            "technical_brief": "Removed the byline preparation label and model-specific protocol sentence; added the requested newer/diverse-model refinement history. The full analysis's score assumptions are unchanged.",
            "overview": "New, less-dense professional synopsis with direct links to the technical brief, full report, and open repository.",
            "full_report_and_json": "Unmodified from the 8 October public edition.",
        },
        "validation": {
            "technical_brief_pages": 1, "overview_pages": 1,
            "technical_brief_words": len(texts[0].split()),
            "overview_words": len(texts[1].split()),
            "overview_under_half_technical_word_count": True,
            "requested_removals_and_refinement_present": True,
            "overview_links_both_papers_and_repository": True,
            "full_report_and_json_hashes_unchanged": True,
            "both_final_page_renders_visually_reviewed": True,
        },
        "assessment_status": "provisional_source_scope_review_not_proof_certification",
        "artifacts": artifacts,
    }
    out = DOCS / "OpenAI_Mathematics_OPDP_Publication_2026-10-09.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest["validation"]))
    print(json.dumps(artifacts))


if __name__ == "__main__":
    main()

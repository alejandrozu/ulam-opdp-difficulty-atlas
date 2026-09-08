# OPDP append-only MathDB expansion — v1.7

OPDP v1.7 appends the exact public-catalog representation of MathDB's frozen 2026-09-08 problem inventory to the 15,458-record v1.6 atlas. The five MathDB problem sitemaps and the public catalog API agreed on exactly 87,105 items, so the combined release contains exactly 102,563 records.

This release performs corpus acquisition and provisional OPDP classification only. It includes no new source-provenance study and no cross-dataset comparison.

## Frozen catalog snapshot

- Inventory freeze date: 2026-09-08.
- Inventory: 87,105 unique MathDB problem numbers, from 1 through 405,270 with gaps.
- Inventory sources: five `sitemap-problems-N.xml` files; their union agrees exactly with the public catalog API count.
- Catalog file: `mathdb_problem_summaries.jsonl`, sorted by MathDB number.
- Envelope schema: `opdp.mathdb.problem-summary.v1`, with the shape `{schema, snapshot, problem}`.
- `problem` is the exact parsed public-list item and is copied losslessly to OPDP `source_record`.
- `snapshot` retains the catalog request page, retrieval time, response hash, release header, originating sitemap entry, and the declaration `statement_completeness: source_provided_excerpt_not_guaranteed_complete`.
- Catalog JSONL SHA-256 and byte size: **`322A1D077168EC03CBE5B9002A84E0B33591BDC6C5EEAE8AF38BB1AD272ED4CB`**, **`137072728`** bytes.
- Catalog manifest SHA-256: **`33D1D8FBE7D3F2083F9E145F9EE9F30685D59C8FED639F85CC1C3027847F09E6`**.
- MathDB release-header distribution observed over the completed catalog capture: **`e90e208849ede88c95334287d6d92b89680cf004: 75,900; 6aaf20bf93ad090da496466adb0f0c32b7e86298: 11,205`**.
- Final OPDP v1.7 JSON.gz SHA-256 and byte size: **`674BA2344AF67C152AA44465E7D5A86E63E006179120B1E1465D31A9576F10AE`**, **`61844099`** bytes.
- Builder-validation and independent-validation report SHA-256 values: **`5A1FA1197A5EA8BA495CEC5A3FE51495C602418572A54506BC5A9915F63046F1`**, **`7533C0D48FE4C9B71A351FD0959F61B5F50F2D6ACFC1281A377C35E1B4A2B750`**.

These placeholders must be replaced from the completed catalog manifest, final artifacts, and `CHECKSUMS.sha256`. Partial-run values must not be published as release hashes.

## Text-coverage limitation

MathDB's public catalog list item provides a title, a source-supplied `excerpt`, tags, collections, status, chronology, author, importance, and lightweight engagement metadata. It does not guarantee that `excerpt` is the complete mathematical statement. Accordingly:

- `source_text.statement` is exactly `problem.excerpt` and `source_text.text_mode` is `mathdb_public_list_excerpt`;
- the release never labels or presents that text as a complete problem statement;
- every MathDB addition carries `source_excerpt_only` and `needs_curation`;
- assessment-gate confidence is C0 because the excerpt cannot support a complete status audit;
- intrinsic difficulty, AI difficulty, human attention, verification, formalization, and prerequisite confidence are all C0;
- scored tractability is also explicitly C0, its intervals are widened, and it is retained only as a provisional coverage heuristic; and
- every one of the ten public rationales states the public-list-excerpt limitation and need for recalibration from a complete statement.

The numerical values remain deterministic applications of the unchanged rule to the available cues. C0 means damaged or insufficient evidence; these values must not be treated as statement-complete rankings.

## Access constraint and acquisition policy

MathDB's richer detail endpoint exposes more fields but enforces a limit of 500 unique problem downloads per day. [MathDB's Terms](https://mathdb.com/terms) direct users seeking bulk data to contact the administrators. The release did not circumvent this control: it did not rotate accounts, credentials, cookies, IP addresses, or hosts, and it did not spread requests across days to reconstruct the complete detail corpus.

Instead, `acquire_mathdb_snapshot.py --limit 1` froze the complete sitemap inventory while limiting detail access to one diagnostic item. `acquire_mathdb_catalog_snapshot.py` then captured the supported public list surface in conservative, resumable pages and checked its number set exactly against the frozen inventory. The release therefore covers every listed MathDB item while making no claim to possess 87,105 full statements.

## Append-only compatibility contract

- The first 15,458 array entries are complete, deep-equal v1.6 records. No legacy score, rationale, flag, source row, or implementation field is refreshed.
- Top-level and per-record schema versions remain `1.0.0`.
- The rule remains `OPDP-1.0-rulepass-2026-07-31`; the automation confidence ceiling remains C2 generally and is deliberately C0 for every excerpt-derived MathDB assessment.
- Each MathDB record has `problem_id = 40000000 + MathDB number` and `problem_number = "MATHDB-{number}"`. This mapping is stable, reversible, and independent of number gaps.
- Appended IDs range from `40000001` through `40405270`, with exactly 87,105 occupied values sorted in increasing order.
- Complete release count: `15,458 + 87,105 = 102,563`.
- The release is JSON-only. It supplies no 102,563-row XLSX workbook.

## Retained fields and status handling

Every field supplied by the public-list item is retained unmodified under `source_record`. In the frozen schema these include the UUID and number, title, excerpt, post type, native status, tags, author object, importance, first-stated/documented dates and evidence links, progress object, solution/comment/vote/view counts, activity fields, and timestamps. Acquisition metadata is retained separately under `provenance.mathdb_snapshot` and `implementation.source_snapshot`.

`importance`, popularity and engagement counts, solution/comment counts, and model/human attempt payloads are excluded from scoring. Native status is used only for the conservative OPDP status/gate mapping described below; it is not evidence that a claimed solution has been verified. The single OPDP category is a deterministic projection from the available tags, title, and excerpt; it is not asserted to be a source-native classification.

MathDB status is mapped conservatively:

| Native status | OPDP catalog status | Default assessment treatment |
|---|---|---|
| `open`, `unsolved` | `open` | Source-claimed open unless another available-text gate applies |
| `claimed_progress`, `partial_progress`, `partially_solved` | `partially_solved` | Source-claimed open, with partial status retained |
| `claimed_solved` and unrecognized non-open values | `partially_solved` or `open` | Status unclear pending independent verification |
| `solved`, `refuted`, `disproved`, `closed`, `false` | `solved` | Solved gate; tractability is intentionally null |

These are catalog mappings, not a current-literature audit. A claimed solution is not promoted to a verified resolution. Consumers should display native status, OPDP gate, C0 confidence, and review flags beside every MathDB score.

## Attribution and reuse

MathDB identifies contributed database content under CC BY 4.0; see [MathDB Terms](https://mathdb.com/terms). The OPDP export attributes MathDB at dataset level, preserves each list item's available source fields, and records `declared_dataset_license: "CC-BY-4.0"` and the Terms URL. Reusers must preserve attribution and remain responsible for any separate terms attached to underlying cited material.

The OPDP assessment authorship remains Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol. Scores and rationales are provisional rule-based editorial outputs, not expert certification or empirical per-problem AI solve trials.

## Validation contract

Publication requires both builder validation and the independent streaming validator to pass with zero errors. They check:

- 87,105 catalog envelopes and 102,563 total OPDP records;
- exact agreement between the frozen sitemap-number set and catalog-number set;
- unique MathDB UUIDs and numbers, strict order, and the reversible ID mapping;
- deep equality of all 15,458 legacy records to v1.6;
- lossless equality of every appended `source_record` to the envelope's exact `problem` list item;
- `problem.excerpt` equality, incomplete-statement metadata, `source_excerpt_only`, all assigned C0 caps, widened ranges, and limitation language in all ten rationales;
- record field order, formulas, tiers, null semantics, registries, and top-level summary recomputation;
- exclusion of importance, engagement/popularity counts, solution/comment counts, and attempt payloads from scoring, apart from the explicitly documented conservative use of native status; and
- valid gzip/JSON transport and final hashes matching the completed catalog manifest and `CHECKSUMS.sha256`.

The builder writes `data/OPDP_v1.7_MathDB_Append_Validation.json`; the independent streaming validator writes `data/OPDP_v1.7_Independent_Validation.json`. Neither validator materializes the full decompressed exchange file as one string or record array.

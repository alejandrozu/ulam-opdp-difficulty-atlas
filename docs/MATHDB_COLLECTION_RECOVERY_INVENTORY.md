# MathDB collection recovery inventory

This is the non-destructive first phase of statement recovery for five named
collection candidates. It records what can be established from frozen
artifacts and authoritative collection indexes without inventing statements,
claiming unsupported MathDB membership, or changing the v1.7 payload.

The machine-readable outputs are
[`MATHDB_COLLECTION_SOURCE_INVENTORY_v1.json`](../data/MATHDB_COLLECTION_SOURCE_INVENTORY_v1.json),
[`MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json`](../data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json),
and
[`MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl`](../data/MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl).
The latter contains hashes and JSON pointers into the frozen v1.6 file, not
copied statements.

## Scope and boundary

The frozen MathDB public-catalog envelope has no native collection ID or
collection label. Therefore neither a title, tag, source URL lead, nor a
similar Ulam record is automatically treated as proof that a MathDB item
belongs to Erdős, AIM, AMR, Kourovka, or Millennium.

The inventory distinguishes three facts that must remain separate:

1. A project-requested target count is a planning number, not a source-verified
   MathDB membership count.
2. Frozen Ulam membership is an exact provenance-field match internal to the
   15,458-record UnsolvedMath v1.6 snapshot.
3. A MathDB title-pattern hit is a discovery cue only. It must be followed by
   a source-bound statement/locator match before a recovery label can be
   assigned.

No source statement is reconstructed, no openness conclusion is made, and no
OPDP score is recalculated in this phase.

## Frozen inputs

| Artifact | Scope | SHA-256 |
| --- | ---: | --- |
| UnsolvedMath OPDP v1.6 | 15,458 statement-bearing records | `49e9be65788e8d5cbfdcba63436eedebc50965ea5b815bb3fee08cf00e7118f3` |
| MathDB public catalogue snapshot | 87,105 exact list items; excerpt-only text basis | `322a1d077168ec03cbe5b9002a84e0b33591bdc6c5eeae8af38bb1ad272ed4cb` |
| Collection/Ulam overlap sidecar | 7,489 metadata-only records | `6299c8dbd5a03ffade37a7de75dee4677480f5c50b3e5d83c9163fd95eaa320a` |

The input catalogue's `source_record_semantics` is “exact parsed public-list
item”; it does not imply access to a complete MathDB statement. Every new
v1.7 record remains excerpt-only until separately recovered.

## Collection-count ledger

“Ulam overlap” below requires all three frozen conditions: the expected
`provenance.source_collection_id`, the expected collection label, and the
collection-specific display-ID pattern. It is a deterministic check rather
than a fuzzy text match.

| Collection | Requested MathDB target | Frozen Ulam overlap | Difference (target − Ulam) | MathDB discovery diagnostic |
| --- | ---: | ---: | ---: | --- |
| Erdős Problems | 1,171 | 632 | +539 | 1,172 strict `Erdős Problem #…` title candidates; discovery-only |
| AIM Workshop Problem Lists | 3,165 | 3,359 | −194 | No safe native signal registered |
| AMR Open Problem Lists | 3,189 | 3,342 | −153 | No safe native signal registered |
| Kourovka Notebook, issue 21 | 150 | 150 | 0 | 150 strict `Kourovka Notebook Problem 21.n` title candidates; discovery-only |
| Millennium Prize Problems (six unresolved) | 6 | 6 | 0 | No safe native signal registered |

The mismatches are intentionally retained rather than reconciled away. They
can reflect collection evolution, different inclusion/status policies,
deduplication, identifier gaps, or a target-count assumption. They do not
measure missing MathDB data, and they do not justify adding or deleting any
record.

In particular, the pinned Erdős upstream YAML contains 1,217 top-level
records at its captured revision, while the project target is 1,171 and the
strict MathDB-title diagnostic yields 1,172 rows. Those are three different,
explicitly non-interchangeable counts.

## Retrieved source artifacts

Only response hashes and metadata were retained; raw HTML, YAML, and PDFs
were not stored by this capture. The access timestamp and HTTP metadata are
in the external-evidence JSON.

| Collection source | Artifact role | Bytes | SHA-256 | Count evidence |
| --- | --- | ---: | --- | --- |
| [Erdős Problems repository YAML](https://github.com/teorth/erdosproblems/blob/3c68e941162f81d650fc886eed34e58bed3a6a01/data/problems.yaml) | Pinned structured inventory | 401,189 | `3e9ab5fb8b479274af52112aec8e7910e9bd9470feef8d4830d67a8d5fd20bcb` | 1,217 top-level YAML records |
| [Erdős Problems site](https://www.erdosproblems.com/) | Public collection presentation | 43,834 | `058d420fead2d809d421e6b3893d837030c9d36f95d93a14de9ac9d99b8b368f` | No count asserted by this capture |
| [AIM Problem Lists](https://aimath.org/problemlists/) | Official collection index | 64,452 | `b2ecd40078f442c28924fe5dfdf01c5a2706953f18a1fb6e774c13542fc55088` | Index only; no aggregate problem count asserted |
| [AMR Resources](https://amathr.org/resources/) | Official problem-list directory | 157,526 | `893fbb79f2c7ae355a37383a46231b4f8a17d5c6addbf55a388190fbf89c09b4` | Directory only; no aggregate problem count asserted |
| [Kourovka issue 21 announcement](https://kourovkanotebookorg.wordpress.com/2026/01/09/the-new-21-st-edition-of-kourovka-notebook/) | Official edition announcement | 84,563 | `e441f78453c6db7bcd20b67309d9e88fa192b216960e5878ea28ee87e70e38fd` | Source announcement says 150 new problems |
| [Clay Millennium Problems](https://www.claymath.org/millennium-problems/) | Official collection landing page | 87,891 | `c9cbf731e04ffa4341f6abb75336f557db7acc320a68d198882f130da4b9e515` | Page is the official source for the six-unresolved collection framing |

The Kourovka page phrase occurs in both metadata and visible content; the
capture validates two occurrences of the same count assertion rather than
mistaking the repetition for two collections.

## What the overlap sidecar provides

For every one of the 7,489 Ulam-overlap records, the JSONL contains:

- the collection key and exact matching rule;
- the v1.6 record index and JSON pointers to the frozen record and statement;
- hashes of the complete frozen record, original source row where available,
  and UTF-8 statement text;
- preserved Ulam provenance URLs and declared dataset license; and
- explicit guards saying `mathdb_membership` is not asserted,
  `recovery_status` is inventory-only, and OPDP recalculation is forbidden.

This makes the existing Ulam statement snapshot a reproducible local source
lead while avoiding a second public copy of potentially third-party source
text. The source snapshot itself remains subordinate to the original
collection document for provenance, currentness, and reuse analysis.

## Reuse and attribution

UnsolvedMath declares CC BY 4.0 for its dataset snapshot, but that does not
supersede rights in a cited paper, workshop PDF, collection book, or web page.
The pinned `teorth/erdosproblems` repository declares Apache-2.0; retain its
license and attribution notices if reusing its structured work. This inventory
does not establish a reuse license for the AIM, AMR, Kourovka, or Clay source
materials. Link to those sources and conduct source-specific rights review
before publishing recovered prose, diagrams, or substantial quotations.

## Next recovery gate

A later record-level recovery may use these inventories only after it can
produce an independently auditable chain:

1. bind a particular MathDB item to a source artifact/identifier, not merely
   a similar title;
2. retain a byte/line/page locator and artifact digest for the actual source
   passage;
3. label the result `exact_source`, `faithful_normalization`,
   `contextual_reconstruction`, or `unresolved` under the recovery protocol;
4. verify current openness independently; and
5. send only `exact_source` or `faithful_normalization` plus a verified-open
   status to a separately versioned OPDP rescoring queue.

## Reproduction

From the repository root, the first command performs the only network work
and requires an explicit acknowledgement. It contacts six URLs named in the
versioned registry, at one request per second, retains no response body, and
writes only hash-level evidence.

```powershell
$py = 'C:\Users\Propietario\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

& $py scripts/mathdb_recovery/capture_collection_source_evidence.py `
  --allow-network `
  --registry scripts/mathdb_recovery/collection_source_registry.v1.json `
  --output data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json

& $py scripts/mathdb_recovery/build_collection_source_inventory.py `
  --registry scripts/mathdb_recovery/collection_source_registry.v1.json `
  --ulam-v16 data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
  --mathdb-catalog ../opdp-v1_7-mathdb-build/mathdb-snapshot-2026-09-08/mathdb_problem_summaries.jsonl `
  --mathdb-manifest ../opdp-v1_7-mathdb-build/mathdb-snapshot-2026-09-08/mathdb_catalog_manifest.json `
  --external-evidence data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json

& $py scripts/mathdb_recovery/build_collection_source_inventory.py --verify-only
```

The builder validates the 87,105-record MathDB catalogue SHA-256 against its
frozen acquisition manifest, requires the expected Ulam overlap counts, and
checks that the generated outputs contain no statement copy or illegal MathDB
membership claim.

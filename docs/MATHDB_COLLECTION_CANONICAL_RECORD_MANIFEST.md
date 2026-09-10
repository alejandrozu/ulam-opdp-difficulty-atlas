# Collection canonical-record manifest

[`MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.jsonl`](../data/MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.jsonl)
is a provenance and recovery-gate sidecar for the five named MathDB collection
candidates. It contains **7,489 exact frozen-Ulam overlap records**, not a
claim that the same records are members of a MathDB collection. The frozen
MathDB public-list catalog still has no native collection-membership field.

The artifact stores identifiers, JSON pointers, SHA-256 bindings, source
locators, acquisition/reuse facts, and recovery gates. It deliberately stores
no statement, title, background text, source excerpt, PDF content, or score.
Its JSON summary is
[`MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.json`](../data/MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.json).

## What is established

| Collection | Frozen-Ulam records | Source leads | Locator quality | Comparison-eligible | Automatic outcome / rescore |
| --- | ---: | ---: | --- | ---: | --- |
| Erdős Problems | 632 | 632 pinned upstream numeric records | Exact YAML line spans | 632 | 0 / 0 |
| AIM Workshop Problem Lists | 3,359 | 455 distinct declared document URLs | Document URL, no passage | 0 | 0 / 0 |
| AMR Open Problem Lists | 3,342 | 223 distinct declared document URLs | Document URL, no passage | 0 | 0 / 0 |
| Kourovka Notebook, issue 21 | 150 | One registered issue-PDF URL | Artifact URL, no page/passage | 0 | 0 / 0 |
| Millennium Prize Problems | 6 | One registered Clay monograph URL | Artifact URL, no page/passage | 0 | 0 / 0 |

For Erdős, the tool made one explicitly authorized request to the pinned
[`teorth/erdosproblems` YAML revision](https://github.com/teorth/erdosproblems/blob/3c68e941162f81d650fc886eed34e58bed3a6a01/data/problems.yaml).
It verified SHA-256
`3e9ab5fb8b479274af52112aec8e7910e9bd9470feef8d4830d67a8d5fd20bcb`,
found 1,217 top-level numeric records, and retained only the numeric ID and
GitHub line span in
[`MATHDB_COLLECTION_ERDOS_RECORD_LOCATORS_v1.json`](../data/MATHDB_COLLECTION_ERDOS_RECORD_LOCATORS_v1.json).
The downloaded YAML body was discarded; it is not in this repository.

“Comparison-eligible” is intentionally much narrower than “recovered.” It
means a future reviewer has an exact hash-bound upstream record locator to
compare with a frozen statement snapshot. It does **not** assign
`exact_source`, `faithful_normalization`, `contextual_reconstruction`, or
`unresolved`, does not certify a statement as self-contained, and does not
say the problem remains open.

## Row contract

Every JSONL row has the following logical components:

- `frozen_ulam`: immutable Ulam ID, JSON pointers, and hashes; the statement
  is referred to only by SHA-256 and is never copied.
- `upstream_identity`: a stable source ID and the strength of the binding.
  For AIM/AMR/Kourovka/Millennium, the status explicitly says that it is only
  a frozen source lead or display-ID candidate.
- `source_locator`: an exact record line span, an exact document URL, or an
  artifact URL, with a quality label that distinguishes those cases.
- `recovery_eligibility`: gates statement comparison separately from an
  automatic recovery label and OPDP recalculation.
- `claims_not_made`: fixed guards against MathDB membership, open-status,
  recovery-outcome, and score claims.

The summary binds the JSONL to the frozen collection inventory, overlap
sidecar, Ulam v1.6 export, source-evidence capture, registry, and Erdős
locator index by SHA-256. A later recovery stage must preserve those bindings
or make an explicit, versioned supersession.

## Rights and retrieval boundary

The structured Erdős repository declares Apache-2.0 for its repository work,
with its own caveat about cited original material. The AIM, AMR, Kourovka, and
Clay sources have no reuse license established by this manifest. Public access
does not grant a right to republish workshop-PDF prose, monograph material,
diagrams, or extensive quotations.

Accordingly, the builder does not crawl the 455 AIM or 223 AMR documents,
download the Kourovka/Clay PDFs, or infer a page locator. These are not
failures hidden by a score: they are explicit `not comparison-eligible` rows.
Before any one of them can become eligible, a source-specific worker must
acquire an authorized artifact at a polite rate, record its digest and exact
page/section/line locator, check reuse terms, and conduct a semantic review.

## Reproduction and validation

The first command is the only network action. It requests one pinned URL,
holds it only in memory, checks its known digest, and emits no raw YAML.
Subsequent builds and verification are local-only.

```powershell
$py = 'C:\Users\Propietario\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

& $py scripts/mathdb_recovery/build_collection_canonical_record_manifest.py `
  --refresh-erdos-locator-index --allow-network

& $py scripts/mathdb_recovery/build_collection_canonical_record_manifest.py --verify-only

& $py -m unittest scripts/mathdb_recovery/tests/test_build_collection_canonical_record_manifest.py -v
```

The validator rejects a changed input hash, copied-statement field, duplicate
record key, nonexact collection count, assignment of an openness/recovery
outcome, or any automatic OPDP-rescoring permission.

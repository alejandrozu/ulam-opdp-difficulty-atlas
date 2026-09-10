# arXiv source-recovery acquisition decision note

**Scope:** recover source evidence for the canonical set of 42,371 normalized arXiv identifiers in the final MathDB arXiv-source manifest, while keeping local working storage below 4 GB.  This note covers acquisition only; it does not alter OPDP scores or release files.

**Decision (2026-09-10): do not automatically launch a 42k source-file crawl without an authenticated AWS Requester Pays account, an agreed egress budget, and a per-source licensing gate.**  The official S3 collection is the appropriate bulk route.  `export.arxiv.org/src/<id>` is a documented individual-source endpoint and a viable *fallback*, but it should not silently substitute for bulk acquisition at this scale.

## What arXiv officially provides

| Need | Official mechanism | Important constraint |
| --- | --- | --- |
| Metadata for an identified paper set | `https://export.arxiv.org/api/query?id_list=<comma-separated IDs>&max_results=<batch size>` | Atom XML; `max_results` must be explicit because the API otherwise defaults to 10 results; no more than one legacy-API request every 3 seconds and one connection at a time. |
| Complete or synchronized metadata | `https://oaipmh.arxiv.org/oai` | OAI-PMH is arXiv's preferred bulk metadata mechanism.  The current OAI identifier form is `oai:arXiv.org:<id>`. |
| Full source files in bulk | S3 bucket `s3://arxiv`, keys `src/arXiv_src_YYMM_NNN.tar` and `src/arXiv_src_manifest.xml` | Requester Pays: authenticated AWS requests must explicitly accept charges.  Chunks are about 500 MB, not one object per article. |
| One article's submitted source | `https://export.arxiv.org/src/<id-or-idvN>` | Standard documented URL, but not a batch API.  Use only as a conservative, resumable fallback. |

Sources: arXiv's [bulk-data guide](https://info.arxiv.org/help/bulk_data.html), [S3 guide](https://info.arxiv.org/help/bulk_data_s3.html), [OAI-PMH guide](https://info.arxiv.org/help/oa/index.html), [identifier/service URL guide](https://info.arxiv.org/help/arxiv_identifier_for_services.html), and [API terms](https://info.arxiv.org/help/api/tou.html).

The source bucket is in `us-east-1` and is requester-pays.  A valid CLI request therefore needs an authenticated AWS identity, permission to read the public bucket, and an explicit payer declaration, for example:

```powershell
aws s3 cp s3://arxiv/src/arXiv_src_manifest.xml .\arXiv_src_manifest.xml `
  --region us-east-1 --request-payer requester --no-progress
```

Do not use anonymous S3 access or omit `--request-payer requester`: requester-pays buckets require authenticated requests and the parameter acknowledges the associated charge.  See the [AWS requester-pays documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ObjectsinRequesterPaysBuckets.html).

## Why S3 is necessary for a corpus-scale run

The arXiv bulk page explicitly identifies S3 as the accepted mechanism for downloading the complete corpus; its source archives are grouped into roughly 500 MB tar files.  The full source collection is measured in terabytes, so neither it nor the Kaggle full-text copy is compatible with a 4 GB local-storage limit.

The current MathDB references are temporally dispersed rather than concentrated in a few archive chunks: a raw URL-field inventory finds identifiers spanning **403 year-months (1992-01 through 2026-08)**.  Even the unrealistic lower bound of one 500 MB source tar per represented month is about **202 GB of transfer** (roughly 197 GiB).  The exact number of necessary tars must be calculated from `src/arXiv_src_manifest.xml` after authenticated access is available; it will generally exceed that lower bound because a month can occupy multiple chunks.

Direct `export.arxiv.org/src` URLs do work for individual papers, but a 42,371-item run is a large crawler in both request count and unknown byte volume.  arXiv asks harvesters to use `export.arxiv.org`, and its older custom-harvesting guidance suggests bursts of four requests per second followed by a one-second sleep.  Separately, the current API Terms impose a global limit for legacy APIs (OAI-PMH, RSS, and the arXiv API) of **one request every three seconds, one connection at a time**.  Because source URLs are not expressly classified there as a legacy API, the safe fallback adopts the *stricter* one-request/three-second, single-worker discipline across all arXiv traffic until arXiv grants a different rate.  At that rate, 42,371 individual source calls alone require at least **35.3 hours**, excluding transfer and retry time.

## Implemented metadata-only acquisition layer

`scripts/mathdb_recovery/acquire_arxiv_metadata.py` implements the first,
non-text stage against the immutable final source manifest.  It is dry-run by
default and will not make a network request until `--allow-network` is passed;
an all-42,371-ID run additionally requires `--allow-full-run`.  It sends only
sequential HTTPS requests of the form
`https://export.arxiv.org/api/query?id_list=<up-to-50-IDs>&max_results=<exact-batch-size>`,
with `Connection: close`, redirects rejected, and at least three seconds
between request starts.  `max_results` is required because the API otherwise
returns only its default ten entries even when more IDs were requested.
The full manifest produces 848 batches and therefore has a nominal rate-limit
floor of about 42.4 minutes, before retries.

Its ignored recovery-run output records an immutable plan, one immutable
response envelope per successful batch, exact request URL, raw Atom-response
SHA-256, parsed title/abstract/category/author metadata, returned version, and
the license or withdrawal indicators provided by the API when present.  The
exact Atom bytes are retained only in each local response envelope; no arXiv
source, PDF, e-print, or HTML body is requested.  Errors are appended to a
separate ledger and a derived metadata index can be safely regenerated from
the immutable envelopes on resume.

The handoff to S3 is intentionally limited: the API layer supplies canonical
IDs, observed/requested versions, and non-authoritative license hints to map
against `src/arXiv_src_manifest.xml`.  It does **not** choose a source tar,
authorize a Requester-Pays charge, establish redistribution rights, locate a
passage, reconstruct a statement, or verify that an open problem is still
open.  Those remain independently gated steps in the source-extraction and
curation workflow.

## Safe staged retrieval specification

1. **Freeze and normalize the source manifest.**  Preserve both `arxiv_id_requested` (including an explicit `vN` when cited) and the versionless canonical work ID.  Normalize URLs from `/abs/`, `/pdf/`, `/src/`, `10.48550/arxiv.*`, and ar5iv forms; keep malformed or ambiguous references in a review queue rather than guessing.

2. **Obtain metadata before source files.**  Use the arXiv API in sequential batches of at most 50 IDs, with a 3-second inter-request delay.  This is 848 calls and a 42.4-minute rate-limited lower bound for 42,371 IDs.  Save only metadata plus a request/response hash.  Use OAI-PMH `GetRecord` with `metadataPrefix=arXivRaw` only when the API result needs an authoritative license, version-history, or source-type check; doing it once per ID would take at least 35.3 hours at the required rate.

3. **License and publication gate.**  Metadata may be stored and shared; raw e-prints and source files may be used for research, but arXiv's terms prohibit serving them unless the copyright holder or the article license permits it.  The default arXiv distribution license does not itself give OPDP a redistribution right.  Record the source license before retaining any verbatim passage in a public repository.  Otherwise publish a locator, hash, and a non-verbatim audit finding, not the raw source or a copied statement.  See [arXiv API Terms](https://info.arxiv.org/help/api/tou.html).

4. **Plan S3 chunks from the manifest.**  Map every normalized ID to the smallest source-tar set using the manifest's `first_item`, `last_item`, checksum, and size fields.  Produce a dry-run budget report (`tar_count`, `expected_bytes`, `maximum_concurrent_bytes=1 archive`) and require a human budget approval before the first large transfer.

5. **Stream, do not archive.**  Fetch one source tar at a time; verify its manifest checksum; extract only target article members; parse text without executing TeX, shell commands, macros, or embedded scripts; cap archive and decompressed-member sizes; emit compact evidence records; then remove the staging material.  Enforce a 3 GB staging ceiling, no concurrent tar downloads, and a hard stop before 4 GB.  Retain source locator, version, object key, archive/member hash, byte offsets or section/page locator, and extraction log—not the complete source corpus.

6. **Recover only auditable problem statements.**  Match the MathDB title/excerpt against retrieved source text and tag each candidate exactly once as:
   - `exact_source` — explicit statement and exact locator found;
   - `faithful_normalization` — notation normalized without changing logical content;
   - `contextual_reconstruction` — statement rebuilt from local context, with required definitions and uncertainty recorded; or
   - `unresolved` — no defensible self-contained statement.

7. **Check status separately.**  A paper's age, abstract, or MathDB label does not establish that a conjecture remains open.  Record a dated independent status search and authoritative evidence.  Keep `source_claimed_open` or `status_unclear` when no current confirmation exists.

8. **Score only reliable recoveries.**  Recalculate OPDP dimensions only after statement identity, source locator, and current-status gate are all sufficient.  Leave the existing provisional assessment and an explicit recovery-status flag in place for every other record.

## Minimal durable ledger

The recovery worker should write append-only JSONL records with at least:

```text
mathdb_problem_id, arxiv_id_requested, arxiv_id_canonical, cited_version,
metadata_endpoint, metadata_sha256, source_license_url, source_object_key,
source_member_path, source_archive_sha256, retrieved_at_utc,
retrieval_status, extraction_status, statement_locator,
recovery_class, title_match_confidence, status_verification_class,
rights_publication_class, failure_reason
```

This makes every statement, non-match, retry, and refusal to publish independently reviewable without retaining a multi-terabyte or copyright-restricted paper mirror.

## Small validation performed

On 2026-09-10, single-record, rate-spaced checks succeeded for the HTTPS API query, the current OAI-PMH `GetRecord` identifier (`oai:arXiv.org:2401.00001`), and an HTTP `HEAD` request to `https://export.arxiv.org/src/2401.00001`.  The latter returned a gzip source archive with a content length and ETag; no source body was downloaded for this validation.  These checks establish endpoint behavior only, not permission or budget for the 42k run.

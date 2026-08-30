# Submission-note template

Adapt this text to the actual solver and disclosures. Do not paste unverified
bracketed fields.

> This solver incorporates the OPDP-Cert SAIR Stage 2 priority adapter v0.1.0,
> contributed by Alejandro Zarzuelo Urdiales and published at
> `https://github.com/alejandrozu/ulam-opdp-difficulty-atlas/tree/[OPDP_COMMIT]/contrib/opdp-cert-sair-stage2`
> under the scoped MIT license. The vendored adapter is based on
> `[OPDP_COMMIT]` and targets EULER `[EULER_COMMIT]`.
>
> The adapter uses text-bound EULER oracle direction plus deterministic
> equation-shape features only for Marathon input ordering. It does not make an
> independent TRUE/FALSE prediction, emit a verdict, generate Lean certificates,
> call a model or network service, or replace any proof/countermodel validation
> gate. The submitted policy is `[baseline or structural_v0]`.
>
> On the four released manifests at official repository commit
> `817a4653bf762584931d49c6714c9fcfab7df66a`, the baseline first bucket matched
> EULER's legacy `_difficulty` for 1,669/1,669 rows with zero input mutations.
> This is compatibility evidence, not a private-score claim. [If and only if
> structural_v0 is submitted: summarize the frozen grouped held-out A/B result,
> including accepted counts and all rejection-status deltas.]
>
> The public contribution was developed with OpenAI Codex assistance for source
> inspection, implementation, testing, and review. [Add all participant, team,
> sponsor, external-support, model/API, dataset, and Contributor Network
> disclosures required by the current SAIR rules.]

Before using the template, replace both commit placeholders with full immutable
hashes and confirm the final policy string. Public source and attribution do not
by themselves settle registration or external-support requirements; ask SAIR if
the collaboration classification is uncertain.

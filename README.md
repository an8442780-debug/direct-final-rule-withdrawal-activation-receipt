# Direct Final Rule Withdrawal Activation Receipt

An Intelligent Contract that turns a sealed set of Federal Register instruments into a conservative activation receipt: `PENDING`, `EFFECTIVE`, `WITHDRAWN`, or `UNRESOLVED`. It is designed for regulatory calendars and public-law registries that must not keep a withdrawn direct final rule active.

## Live Deployment

- Network: GenLayer Studio Dev, Chain ID `61997`
- Contract: [`0x5c2E06CDF0962e1A9FbB03c63E1Afd7376CEf40C`](https://explorer-studio-dev.genlayer.com/address/0x5c2E06CDF0962e1A9FbB03c63E1Afd7376CEf40C)
- Deployer: `0x8581c4a532dd3f9b163b12809b1bd089f367147f`
- Deploy receipt: [`0x2c4e83dec361c41d797736669c9309fae3d557f73cf1247d562c63a4335c9fce`](https://explorer-studio-dev.genlayer.com/tx/0x2c4e83dec361c41d797736669c9309fae3d557f73cf1247d562c63a4335c9fce)

The deployed bytecode matches the reviewed source revision. Every submitted E2E write used a finalized, accepted Studio Dev receipt and was followed by an authoritative readback. The public source tree contains the inputs needed to reproduce the local scenarios; live Federal Register responses in the E2E run did not expose all required markers, so the contract correctly remained inactive and fail-closed.

| Scenario | Receipt and result |
| --- | --- |
| Final-rule withdrawal assessment | [`0x0f7e8e453058834bccfe3e7adf3022493623e3b846fe90f541a758b7418c7447`](https://explorer-studio-dev.genlayer.com/tx/0x0f7e8e453058834bccfe3e7adf3022493623e3b846fe90f541a758b7418c7447) — finalized consensus execution, `UNRESOLVED`, `WITHDRAWAL_MARKER_MISSING`, inactive |
| Replay of the same assessment | [`0xc282b017204e22bd27530f0e85237b62a9bd7beb5b56743037ee9876c7091deb`](https://explorer-studio-dev.genlayer.com/tx/0xc282b017204e22bd27530f0e85237b62a9bd7beb5b56743037ee9876c7091deb) — same assessment id and count, proving idempotency |
| Cross-docket counterexample | [`0xd16cf03c885df1ae39e52b0f2ca52341c684198330efe333d1f57fd69b7b7100`](https://explorer-studio-dev.genlayer.com/tx/0xd16cf03c885df1ae39e52b0f2ca52341c684198330efe333d1f57fd69b7b7100) — `UNRESOLVED`, identity mismatch, inactive |

## Problem and why consensus matters

A direct final rule, a correction, and a later withdrawal can have different Federal Register document numbers while sharing a docket and RIN. A date-only database can therefore reactivate a rule that was withdrawn after significant adverse comment. Each validator independently refetches the sealed official URLs and derives the same consequential fields. A normal backend is sufficient when the operator trusts one controlled source and does not need independent public-source reconciliation; GenLayer is useful when the decision must be reproducible across validators and fail closed when sources disagree or disappear.

## How it works

1. The owner creates a lineage with agency, docket, RIN, originating document, originating citation, and expected effective day.
2. The owner adds two or three official Federal Register instruments and their canonical SHA-256 fingerprints, then seals the lineage.
3. `assess_activation` runs the sealed URLs through the Equivalence Principle. Validators check identity, dates, conditional-effect language, correction markers, withdrawal markers, and the fingerprints.
4. Strict equality is applied to the canonical decision JSON. Only after consensus is reached does the contract store the assessment and expose its state through deterministic views.

## State model and invariants

Lineages begin as `DRAFT`, become assessable after sealing, and then materialize `PENDING`, `EFFECTIVE`, `WITHDRAWN`, or `UNRESOLVED`. A superseded lineage records its replacement and cannot be assessed again. `WITHDRAWN` always overrides a scheduled effective day. Missing, unavailable, malformed, cross-identity, or hash-mismatched evidence produces `UNRESOLVED` and `rule_active=false`. Assessment days are monotonic, and replaying the same lineage/day returns the original assessment id without creating a duplicate.

## Public API

- `create_lineage(...)` creates an owner-controlled draft.
- `add_instrument(...)` records an official originating, correction, or withdrawal source.
- `seal_lineage(lineage_id)` freezes a two- or three-source evidence set.
- `assess_activation(lineage_id, as_of_day)` performs consensus reconciliation and stores one receipt.
- `supersede_lineage(lineage_id, replacement_lineage_id)` links a later sealed revision with the same complete identity.
- `get_lineage`, `get_instrument`, and `get_assessment` expose authoritative readback.
- `is_rule_active(lineage_id)` is the deterministic oracle view for integrators; it returns `true` only for an `EFFECTIVE` lineage.

Integration patterns include a calendar service gating publication on `is_rule_active`, a registry showing the receipt fields from `get_assessment`, and a downstream contract that reads `get_lineage` before accepting a rule identifier.

## Consensus Binding Matrix

| Field | Source | Stored? | Downstream effect | Validator check | Binding mode | Differential test |
| --- | --- | --- | --- | --- | --- | --- |
| `identity_match` | Agency, docket, RIN, FR documents and citation | Yes | Rejects activation when false | Every validator checks all identity fragments | Exact canonical JSON | `test_identity_mismatch_fails_closed` |
| `conditional_effect_clause` | Originating source text | Yes | Required before any active state | Validators require both conditional markers | Exact boolean | `test_non_success_source_fails_closed` |
| `corrected_date_state` | Correction effective date | Yes | Selects `PENDING` vs `EFFECTIVE` | Validators check date and marker | Exact ISO day | `test_pending_then_effective_without_withdrawal` |
| `terminal_action` and `terminal_effective_day` | Withdrawal source | Yes | Withdrawal is terminal and inactive | Validators require withdrawal markers and date | Exact fields | `test_withdrawal_overrides_corrected_effective_date` |
| `status` and `rule_active` | Deterministic derivation from the checked fields | Yes | Public lifecycle and oracle view | Validators compare the full canonical result | Derived deterministically | `test_identity_mismatch_fails_closed` and pending/effective test |
| `evidence_fingerprint` | Canonical source fingerprints | Yes | Auditable evidence identity | Validators recompute hashes | Exact SHA-256 | `test_source_fingerprint_mismatch_fails_closed` |

## Security and failure behavior

Source pages and returned text are untrusted. URLs are restricted to Federal Register HTTPS, fields reject delimiter injection, responses are bounded by the runtime, and every external failure is fail-closed. Nondeterministic closures capture only immutable memory values and do not mutate storage. Owner-only writes, sealed lineage checks, source hash validation, expected-date enforcement, cross-identity supersession checks, monotonic assessment days, and idempotent replay protect the state machine. The contract does not crawl search results, classify comments, issue legal advice, or send alerts.

## Verification

The pinned toolchain is GenVM Manager `v0.6.0-rc5`, runner `py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng`, `genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0rc2`, and `genvm-linter 0.11.1rc2`.

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:GENVM_VERSION = 'v0.6.0-rc5'
genvm-lint check contracts/direct_final_rule_withdrawal_activation_receipt.py
pytest tests/direct -q
```

The verified local result is lint PASS, schema validation PASS, and `8 passed`. The scenario inputs used by the tests and the Studio Dev workflows are in [`samples/scenarios.json`](samples/scenarios.json).

## Consensus engineering lessons

- Consensus must bind the fields that change lifecycle state, not only an explanation.
- `UNRESOLVED` is safer than guessing when a public source omits a marker.
- A finalized receipt and a materialized readback are separate release evidence.
- A withdrawal is terminal even when an earlier correction supplied an effective date.
- Complete lineage identity, including the originating citation, is required for supersession.

## Repository structure

```text
contracts/direct_final_rule_withdrawal_activation_receipt.py
tests/direct/test_direct_final_rule_withdrawal_activation_receipt.py
samples/scenarios.json
README.md
requirements.txt
LICENSE
.gitignore
```

## Limitations

The contract verifies the sealed source text and fingerprints supplied by its owner. It does not decide whether a public comment is legally significant, monitor future publications, or replace legal review. A later correction or terminal action must be represented by a new sealed lineage and linked with `supersede_lineage`.

## License

MIT; see [`LICENSE`](LICENSE).

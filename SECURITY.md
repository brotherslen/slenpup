# Security notes: PTCommitment

Static analysis of `src/PTCommitment.sol`. Every finding is listed with what was done about it. Raw output: `audit/slither.txt`, `audit/aderyn-report.md`.

Tools: Slither 0.11.6 (102 detectors), Aderyn 0.6.8 (88 detectors), forge-lint (Foundry 1.7.1). Compiler: solc 0.8.37.

## Fixed

| Tool | Finding | Fix |
| --- | --- | --- |
| Slither `uninitialized-local` | `count` in the constructor declared without an initializer | Initialized to `0` explicitly. Behaviour unchanged (Solidity zero-initializes); silences the detector. |

## Justified (left as is)

| Tool | Finding | Why it stays |
| --- | --- | --- |
| Slither `timestamp`, forge-lint `block-timestamp` (8 sites) | `block.timestamp` used in comparisons | Time windows are the whole point of the contract. On Base (OP Stack) each L2 block's timestamp is fixed at the parent's plus 2 seconds, so the sequencer can't pick an arbitrary value (recalled, not re-verified against OP Stack docs). Even a sequencer that could skew time by seconds only moves a window edge by seconds; with a 6h grace that doesn't decide a day. |
| Slither `incorrect-equality` | `amount == 0` in `reclaimUnfunded` | The check only rejects a no-op transfer. A donation can raise the balance but never block the call; there's no state that depends on the exact balance. |
| Slither `unused-return` | Third return of `ECDSA.tryRecover` (`errorArg`) ignored | `errorArg` is only diagnostic detail. Any `err != NoError` reverts `InvalidSignature`, so nothing is lost. |
| Slither `costly-loop`, Aderyn L-2 (2 sites) | `forfeitMany` updates `unresolvedDays` and `totalCredited` in storage per day | The loop is capped at 256 entries. The worst case (256 days in one call) measures 6.6M gas in `test_forfeitMany_256GasBound`, under Base's block gas limit. Batching the counter writes would save a few percent and add a second code path; I kept the single `_forfeit` path for auditability. |
| Aderyn L-1 Centralization risk (`sweep`, `proposeVerifier`, `acceptVerifier`) | `onlyOwner` functions | These are the only privileged functions and they're in the spec. `sweep` can't touch credited funds and requires every day resolved. Verifier rotation can only change who signs claims, and claims only pay the owner. None of them can move locked funds or redirect payouts. |
| Aderyn L-3 Loop contains `revert` | `forfeitMany` reverts on an inactive day or an open window | Both conditions are fixed by the caller's input and the clock; no third party can make a valid batch start reverting. The one condition a third party *can* change (resolving a day first via `forfeit`) is skipped, not reverted, so front-running can't grief a batch. |

## Properties checked by tests rather than tools

| Property | Where |
| --- | --- |
| Tokens only leave to `owner` or `beneficiary` | `invariant_tokensOnlyLeaveToPayees`, `testFuzz_lifecycle`, `test_fullLifecycle` (the mock token logs every recipient) |
| Balance always covers credits plus unresolved tranches | `invariant_balanceCoversCredits*` |
| Exact token conservation | `invariant_tokenConservation` |
| Each day resolves once; rest days never change | `invariant_daysResolveOnceAndRestDaysUntouched` |
| Claim and forfeit windows are disjoint and cover all time after `dayStart` | `testFuzz_claimXorForfeit` |
| Signature replay across day, deployment, chain; wrong signer; high-`s`; stale challenge | `SignatureTest` |
| Reentrancy through a hostile token | `test_reentrantTokenCannotReenterWithdraw` |
| Blacklisted payee isolation | `test_blacklisted*` (mock), `test_realBlacklistIsolation` (fork, real Circle blacklister) |

Mutation check: 12 hand-made mutants (off-by-one windows, wrong payee, missing signer/timelock/payee/resolution checks, sweep touching credits, etc.) each fail between 3 and 13 tests.

## Known limitations (accepted in SPEC.md §8)

- The owner holds the verifier key and can sign claims without a session.
- `seedDay` is permissionless and uses the previous block hash, so the owner's relayer can grind a few blocks for easy targets.
- A USDC-blacklisted owner or beneficiary strands that party's credits until unblacklisted. Blacklisting the contract itself strands everything.
- A lost owner key strands owner credits and the final sweep.

## Build environment note

The sandbox that produced this build couldn't reach binaries.soliditylang.org, so it compiled with solc-js 0.8.37 (the official Emscripten build from npm) behind a small wrapper. Same compiler version and settings should produce identical bytecode. Before deploying, rebuild with native solc 0.8.37 on your machine and confirm `forge test` still passes; Basescan verification will use native solc.

# PTCommitment — contract spec

Status: v1, signed off 2026-10-03. Implemented in `src/PTCommitment.sol`. Contract design decisions are settled (§9). The remaining open items are deploy-time parameters and don't change the code.

## 1. What this contract is

One immutable contract per commitment. The owner locks `totalRequired` USDC before `startTime`. Each active day pays `trancheAmount` to the owner if a verifier-signed claim for that day lands inside the day's claim window, otherwise to the beneficiary. Payouts are credited, then pulled with `withdraw()`.

## 2. Threat model

The owner is honest but lazy. The contract's job is to make skipping a session cost money. The owner holds the verifier key, so the contract cannot and does not try to stop deliberate forgery (signing a claim with no session behind it). It must stop every *passive* way out: nothing the owner can do without the verifier key, a session, or a hostile beneficiary choice should return locked funds.

External attackers are anyone on chain, including whoever holds the relayer key or a stolen verifier key. They must not be able to:
- move tokens to any address other than `owner` or `beneficiary`,
- resolve a day the wrong way (claim a day with an invalid signature, forfeit a day whose window is still open),
- brick the contract (block claims, forfeits, withdrawals, or sweeps for others).

Because every token transfer goes to one of two immutable addresses, a stolen verifier or relayer key can at worst credit the owner's own tranches early. A stolen relayer key can do nothing a stranger can't already do.

Operational failures and their defined outcomes:

| Failure | Outcome |
| --- | --- |
| Verifier key lost | Owner calls `proposeVerifier`, then `acceptVerifier` 48h later. Days whose window closes during the gap forfeit. |
| NUC down, no claim submitted in window | Day forfeits to beneficiary. |
| Nobody calls `seedDay(d)` | Day can't be claimed; it forfeits after the window. |
| Relayer submits claim after window closes | Tx reverts `ClaimWindowClosed`; day forfeits. |
| Beneficiary blacklisted by USDC | Owner claims and withdrawals unaffected. Beneficiary credits sit in the contract until unblacklisted. **Stranded indefinitely**, see §9. |
| Owner blacklisted by USDC | Beneficiary unaffected. Owner credits and the final sweep are stuck until unblacklisted. **Stranded indefinitely**, see §9. |
| Contract address blacklisted by USDC | Everything is stuck. Not mitigable on chain. |
| Owner key lost | Owner credits, verifier rotation, and sweep become unreachable. Claims still work with the current verifier key. |
| Typo in constructor params | Constructor rejects structurally invalid params (§4). A wrong-but-valid address (e.g. beneficiary) is caught only by the deploy script's print-and-confirm step and by checking on Basescan before `fund()`. Nothing is at risk until `fund()` is called. |
| Not funded by `startTime` | Contract is dead. Any token balance can be returned to the owner (§5.2). |
| Tokens sent to the contract directly | Never counted as funding. Recovered by `reclaimUnfunded` (dead contract) or `sweep` (after all days resolve). |

## 3. Pinned stack

- Solidity `0.8.37` (latest per npm `solc` as of 2026-10-03; I'll re-check against soliditylang.org at build time), `pragma solidity 0.8.37;` exactly.
- Foundry, `evm_version = cancun` (Base supports it).
- OpenZeppelin Contracts `v5.6.1` (latest per npm as of 2026-10-03), imports limited to `SafeERC20`, `IERC20`, `ECDSA`, `EIP712`, `ReentrancyGuard`.
- No proxies, upgradeability, delegatecall, selfdestruct, inline assembly, `receive`, or `fallback`. The contract has no payable functions, so ETH sends revert.
- USDC on Base: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`. **Recalled, not yet verified.** I'll confirm it against Circle's docs and cite the page before the fork test.

## 4. Constructor and immutables

All of these are `immutable`. None can change after deploy. All are `uint256` in code (immutables don't pack, so narrower types only add casts).

| Name | Type | Validation (revert if violated) |
| --- | --- | --- |
| `owner` | `address` | `!= 0` |
| `beneficiary` | `address` | `!= 0`, `!= owner`, `!= address(this)`, `!= token` (likewise `owner != address(this)`, `owner != token`). Must be an address that can send a transaction calling `withdraw()`; see §8. |
| `token` | `IERC20` | `!= 0`, has code |
| `startTime` | `uint64` | `> block.timestamp`, `<= block.timestamp + 365 days` (`MAX_START_DELAY`, catches a milliseconds-for-seconds typo that would otherwise lock funds for millennia), `% 3600 == 0` (on the hour). Deploy value: 04:00 local, converted to UTC at deploy time (§9) |
| `numDays` | `uint16` | `1..256` |
| `scheduleBitmap` | `uint256` | `!= 0`; no bit set at index `>= numDays` |
| `trancheAmount` | `uint256` | `> 0` |
| `graceSeconds` | `uint32` | `<= MAX_GRACE`, where `MAX_GRACE = 43200` (12h) is a contract constant. Deploy value: `21600` (6h) |
| `activeDayCount` | `uint16` | computed: popcount of `scheduleBitmap` |
| `totalRequired` | `uint256` | computed: `trancheAmount * activeDayCount` (checked math) |

The initial `verifier` is a constructor argument (`!= 0`) but lives in storage because it can rotate.

EIP-712 domain: `name = "PTCommitment"`, `version = "1"`, plus `chainId` and `verifyingContract` (OZ `EIP712` adds both and rebuilds the separator if `chainId` changes).

## 5. Storage

| Variable | Type | Meaning |
| --- | --- | --- |
| `funded` | `bool` | Set once by `fund()`. Never by a balance check, so a direct transfer can't activate the contract. |
| `verifier` | `address` | Current signing key. |
| `pendingVerifier` | `address` | Proposed key, `0` if none. |
| `pendingVerifierReadyAt` | `uint256` | Earliest `acceptVerifier` time. |
| `dayState` | `mapping(uint256 => DayState)` | `Unseeded` (default), `Seeded`, `Claimed`, `Forfeited`. |
| `challenge` | `mapping(uint256 => bytes32)` | Written once by `seedDay`. |
| `unresolvedDays` | `uint256` | Starts at `activeDayCount`, decremented on each claim or forfeit. |
| `credit` | `mapping(address => uint256)` | Withdrawable balance, only ever keyed by `owner` or `beneficiary`. |
| `totalCredited` | `uint256` | Sum of current `credit` values (so sweep needs no loop). |

### 5.1 Day state machine

Applies to active days only. Inactive (rest) days have no state and every day-function reverts `InactiveDay` for them.

```
Unseeded ──seedDay──► Seeded ──claim──► Claimed
    │                    │
    └──────forfeit───────┴──forfeit──► Forfeited

seedDay, claim : dayStart(d) <= t <= claimDeadline(d)
forfeit        : t > claimDeadline(d)
```

`Claimed` and `Forfeited` are terminal. Claim and forfeit windows are disjoint at the second: claim requires `t <= claimDeadline`, forfeit requires `t > claimDeadline`.

Time definitions for day `d` (all `uint256` math):
- `dayStart(d) = startTime + d * 86400`
- `dayEnd(d) = dayStart(d) + 86400` (exclusive end of the day itself)
- `claimDeadline(d) = dayEnd(d) + graceSeconds` (inclusive last second a claim can land)

### 5.2 Contract lifecycle

- **Pending**: `!funded && t < startTime`. `fund()`, verifier rotation, and views work.
- **Active**: `funded`. Day functions, withdrawals, verifier rotation work. Sweep works once `unresolvedDays == 0`.
- **Dead**: `!funded && t >= startTime`. `reclaimUnfunded()`, verifier rotation, and views work; nothing can activate it. Terminal.

## 6. Functions

All state-changing functions are `nonReentrant` and follow checks-effects-interactions. Errors are custom errors. Every state change emits an event.

| Function | Caller | Preconditions | Effects | Event |
| --- | --- | --- | --- | --- |
| `fund()` | anyone | `!funded`, `t < startTime` | `safeTransferFrom(msg.sender, this, totalRequired)`; require balance delta `== totalRequired`; `funded = true`. The payer is not recorded and gets no special rights. | `Funded(payer, amount)` |
| `reclaimUnfunded()` | anyone | Dead (`!funded && t >= startTime`), balance `> 0` | Transfer entire token balance to `owner` | `UnfundedReclaimed(amount)` |
| `seedDay(d)` | anyone | `funded`, `d` active, `Unseeded`, `dayStart(d) <= t <= claimDeadline(d)` | `challenge[d] = keccak256(abi.encode(blockhash(block.number - 1), d, address(this)))`; state `Seeded` | `DaySeeded(d, challenge)` |
| `claim(d, videoHash, score, sig)` | anyone | `funded`, `d` active, `Seeded`, `dayStart(d) <= t <= claimDeadline(d)`, `ECDSA.tryRecover(digest, sig)` returns no error and `verifier` | State `Claimed`; `unresolvedDays--`; `credit[owner] += trancheAmount`; `totalCredited += trancheAmount` | `DayClaimed(d, videoHash, score)` |
| `forfeit(d)` | anyone | `funded`, `d` active, `Unseeded` or `Seeded`, `t > claimDeadline(d)` | State `Forfeited`; `unresolvedDays--`; `credit[beneficiary] += trancheAmount`; `totalCredited += trancheAmount` | `DayForfeited(d)` |
| `forfeitMany(days)` | anyone | `days.length <= 256`; each day passes `forfeit`'s checks except already-resolved days, which are **skipped** (so a front-run single `forfeit` can't grief the batch) | As `forfeit`, per day | `DayForfeited(d)` per day |
| `withdraw()` | owner or beneficiary | `credit[msg.sender] > 0` | Zero credit, reduce `totalCredited`, then `safeTransfer(msg.sender, amount)` | `Withdrawn(account, amount)` |
| `proposeVerifier(newKey)` | owner | `funded` not required; `newKey != 0`, `!= verifier` | `pendingVerifier = newKey`, `readyAt = t + 48h` (a new proposal overwrites and restarts the clock) | `VerifierProposed(newKey, readyAt)` |
| `acceptVerifier()` | owner | `pendingVerifier != 0`, `t >= readyAt` | `verifier = pendingVerifier`; clear pending | `VerifierRotated(old, new)` |
| `sweep()` | owner | `funded`, `unresolvedDays == 0`, `balance > totalCredited` | Transfer `balance - totalCredited` to `owner` | `Swept(amount)` |

The old verifier stays valid during the 48h timelock. A claim signed by the old key and submitted after rotation fails; the verifier must re-sign.

### 6.1 Claim signature

```
Claim(uint256 day,bytes32 challenge,bytes32 videoHash,uint32 score)
```

`challenge` is read from `challenge[d]` in storage, never from calldata. Digest is `_hashTypedDataV4(keccak256(abi.encode(CLAIM_TYPEHASH, d, challenge[d], videoHash, score)))`. Recovery uses `ECDSA.tryRecover` and reverts `InvalidSignature` on any error or mismatch; OZ rejects high-`s` and the zero address.

The contract does not interpret `score`; there is no on-chain minimum. The verifier only signs on a pass, and `score` is carried for the record and emitted. The verifier sets it to the number of exercise positions confirmed that day, and `videoHash` to sha256 over the passing clips' sha256 digests in session order.

Front-running a claim is harmless: whoever submits it, the tranche is credited to `owner`.

### 6.2 Views

`dayStart`, `dayEnd`, `claimDeadline`, `isActive(d)`, `claimDigest(d, videoHash, score)` (so the verifier can cross-check its EIP-712 encoding against the chain), plus the public getters.

## 7. Invariants

1. `token.balanceOf(this) >= totalCredited` at all times.
2. While funded: `token.balanceOf(this) >= totalCredited + unresolvedDays * trancheAmount`.
3. While funded: `claimedDays + forfeitedDays + unresolvedDays == activeDayCount`.
4. While funded: `(claimedDays + forfeitedDays) * trancheAmount == totalCredited + totalWithdrawn`.
5. Each active day transitions out of `Unseeded`/`Seeded` at most once, and only into `Claimed` or `Forfeited`.
6. Inactive days never leave the default state.
7. `credit[x] == 0` for every `x` other than `owner` and `beneficiary`.
8. Tokens only ever leave to `owner` or `beneficiary`.
9. `challenge[d]` never changes after it's set, and is never set before `dayStart(d)`.
10. `funded` is set at most once, and only before `startTime`.
11. `verifier` changes only via `acceptVerifier`, and only after the pending key has waited 48h.
12. No function can transfer tokens out of the contract while it's funded with `unresolvedDays > 0`, other than `withdraw` of already-credited amounts.

Invariant 4 replaces the brief's "claimed + forfeited + unresolved × tranche + withdrawn == totalRequired", which double-counts withdrawn amounts (they're already inside claimed/forfeited). Invariants 3 and 4 together say the same thing correctly.

## 8. Escape hatches and stuck-funds paths I found

Ways the owner could get money back without doing sessions:
- **Holding the verifier key.** Accepted by the threat model.
- **Grinding the challenge.** Closed by design (2026-10-04). `seedDay` is permissionless and `blockhash(block.number - 1)` is known before the tx lands, so the owner's relayer could pick among a few candidate challenges. That mattered while the challenge also set rep and hold targets. The verifier now confirms each exercise's position held for 5 seconds and derives only the spoken words from the challenge, so no candidate is easier than another.
- **Beneficiary under the owner's control.** The contract can only check `beneficiary != owner`. The commitment only bites if the beneficiary is someone you'd rather not pay.
- **Grace window overlap.** Day `d`'s claim window and day `d+1`'s overlap for `graceSeconds`. With a 04:00 boundary and 6h grace, day `d`'s deadline is 10:00 the next morning, so a missed evening can be made up before 10:00 alongside that morning's session. `MAX_GRACE = 12h` keeps a deploy typo from widening this past half a day.

- **Verifier rotation.** After 48h the owner can install any key as verifier. That's no more power than already holding the verifier key, and the timelock doesn't constrain the owner. Its only job is key-loss recovery. A thief holding the owner key gets the same power, which is one more reason the owner key lives on a hardware wallet.

Verifier requirements that came out of the contract review (phase 2):
- Read `challenge[d]` from a finalized block before deriving words or signing (built: `chain.wait_finalized`). A reorg of the `seedDay` tx changes the stored challenge and invalidates any signature over the old one.
- One video, one day. In the grace overlap, days `d` and `d+1` can both be seeded, so one recording could contain both days' words. The verifier matches a clip to the first of a day's words spoken in it and keeps a ledger of video hashes; a video that counted for one day is refused for any other (built 2026-10-04, `verifier/ptverifier/session.py`).

Ways funds could get stuck:
- A beneficiary (or owner) that can't call `withdraw()`: an exchange deposit address, or a contract with no generic call function. Payouts are pull-only, so its credits sit forever. Choose a beneficiary address controlled by a person with a normal wallet. (Decided 2026-10-04: the beneficiary has a normal wallet, so `withdrawFor` isn't built.)
- Blacklisted owner or beneficiary (§2 table). The brief forbids redirecting payouts, so these credits wait until Circle unblacklists. I've kept that behaviour.
- Lost owner key strands owner credits and the final sweep. Mitigation is off-chain: seed phrase backup for the hardware wallet.
- Non-USDC tokens sent by mistake are stuck forever. Accepted: `sweep` handles USDC only.

No path found that strands funds when both addresses are healthy and the owner key exists. Every active day reaches a terminal state through `forfeit`, which anyone can call, so `sweep` is always eventually reachable.

## 9. Decisions

Settled (2026-10-03):

| Decision | Outcome |
| --- | --- |
| Grace window | 6h (`graceSeconds = 21600`) |
| Day boundary | 04:00 local |
| `MAX_GRACE` | 12h contract constant |
| Unfunded reclaim | `reclaimUnfunded()` sends the whole balance to `owner`; no funder address. This replaces the brief's "funder reclaim" exception, so invariant 8 holds with no exception. |
| On-chain score floor | None; the verifier gates passes |
| Sweep scope | USDC only |
| `acceptVerifier` caller | Owner only |

Still open. None of these change the contract code; they're constructor arguments or verifier config:
1. Stake size and number of days (`trancheAmount`, `numDays`). Max 256 days per contract.
2. Schedule bitmap, and which days map to PT-A, PT-B, Walk-A, rest. The contract only stores active/rest. The session-type mapping lives in the verifier's config.
3. Beneficiary address.
4. Time zone for the 04:00 boundary: America/Chicago for now, passed to the deploy helper as a parameter, never hard-coded. The chain has no DST, so the boundary is fixed in UTC and moves by an hour locally twice a year. Pick whether 04:00 holds in standard time (05:00 boundary, 11:00 deadline in summer) or daylight time (03:00 boundary, 09:00 deadline in winter).
5. Video retention period (verifier only).

Proposed, not built (per "propose, don't build"):
- ~~`withdrawFor(account)`~~: declined 2026-10-04. The beneficiary has a normal wallet and can call `withdraw()` itself.
- `usedVideoHash` mapping to reject reusing one video for two days. Words already make this fail at the verifier, so it's belt-and-braces.

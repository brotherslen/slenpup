# PTCommitment — contract spec (draft, awaiting sign-off)

Status: draft v0. No code exists yet. Items marked **[DECISION]** need an answer before this is final; my proposed default is given for each.

## 1. What this contract is

One immutable contract per commitment. The owner locks `totalRequired` USDC before `startTime`. Each active day pays `trancheAmount` to the owner if a verifier-signed claim for that day lands inside the day's claim window, otherwise to the beneficiary. Payouts are credited, then pulled with `withdraw()`.

## 2. Threat model

The owner is honest but lazy. The contract's job is to make skipping a session cost money. The owner holds the verifier key, so the contract cannot and does not try to stop deliberate forgery (signing a claim with no session behind it). It must stop every *passive* way out: nothing the owner can do without the verifier key, a session, or a hostile beneficiary choice should return locked funds.

External attackers are anyone on chain, including whoever holds the relayer key or a stolen verifier key. They must not be able to:
- move tokens to any address other than `owner` or `beneficiary` (or `owner` via pre-activation reclaim, see §5.2),
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

All of these are `immutable`. None can change after deploy.

| Name | Type | Validation (revert if violated) |
| --- | --- | --- |
| `owner` | `address` | `!= 0` |
| `beneficiary` | `address` | `!= 0`, `!= owner`, `!= address(this)` |
| `token` | `IERC20` | `!= 0`, has code |
| `startTime` | `uint64` | `> block.timestamp`, `% 3600 == 0` (on the hour; see [DECISION] day boundary) |
| `numDays` | `uint16` | `1..256` |
| `scheduleBitmap` | `uint256` | `!= 0`; no bit set at index `>= numDays` |
| `trancheAmount` | `uint256` | `> 0` |
| `graceSeconds` | `uint32` | `<= MAX_GRACE` (proposed `MAX_GRACE = 86400 - 1`, so grace can never reach a full extra day) |
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
| `pendingVerifierReadyAt` | `uint64` | Earliest `acceptVerifier` time. |
| `dayState` | `mapping(uint256 => DayState)` | `Unseeded` (default), `Seeded`, `Claimed`, `Forfeited`. |
| `challenge` | `mapping(uint256 => bytes32)` | Written once by `seedDay`. |
| `unresolvedDays` | `uint16` | Starts at `activeDayCount`, decremented on each claim or forfeit. |
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

- **Pending**: `!funded && t < startTime`. Only `fund()` and views work.
- **Active**: `funded`. Day functions, withdrawals, verifier rotation work. Sweep works once `unresolvedDays == 0`.
- **Dead**: `!funded && t >= startTime`. Only `reclaimUnfunded()` and views work. Terminal.

## 6. Functions

All state-changing functions are `nonReentrant` and follow checks-effects-interactions. Errors are custom errors. Every state change emits an event.

| Function | Caller | Preconditions | Effects | Event |
| --- | --- | --- | --- | --- |
| `fund()` | anyone | `!funded`, `t < startTime` | `safeTransferFrom(msg.sender, this, totalRequired)`; require balance delta `== totalRequired`; `funded = true` | `Funded(funder, amount)` |
| `reclaimUnfunded()` | anyone | Dead (`!funded && t >= startTime`), balance `> 0` | Transfer entire token balance to `owner` | `UnfundedReclaimed(amount)` |
| `seedDay(d)` | anyone | `funded`, `d` active, `Unseeded`, `dayStart(d) <= t <= claimDeadline(d)` | `challenge[d] = keccak256(abi.encode(blockhash(block.number - 1), d, address(this)))`; state `Seeded` | `DaySeeded(d, challenge)` |
| `claim(d, videoHash, score, sig)` | anyone | `funded`, `d` active, `Seeded`, `dayStart(d) <= t <= claimDeadline(d)`, `ECDSA.recover(digest, sig) == verifier` | State `Claimed`; `unresolvedDays--`; `credit[owner] += trancheAmount`; `totalCredited += trancheAmount` | `DayClaimed(d, videoHash, score)` |
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

The contract does not interpret `score`. The verifier only signs on a pass; `score` is carried for the record and emitted. **[DECISION]** confirm, or tell me if you want an on-chain minimum.

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
- **Grinding the challenge.** `seedDay` is permissionless and `blockhash(block.number - 1)` is known before the tx lands. The owner's relayer could simulate, wait a few 2-second blocks, and only seed when targets come out at the low end of each range. That's active cheating, so it's in-model, but it's cheap and scriptable. The off-chain fix is to make targets matter less than words (words defeat pre-recording; targets only vary effort within a range you already chose). I'm not changing the contract for this.
- **Beneficiary under the owner's control.** The contract can only check `beneficiary != owner`. The commitment only bites if the beneficiary is someone you'd rather not pay.
- **Grace window overlap.** With 18h grace, day `d`'s claim window and day `d+1`'s overlap for 18 hours. You could skip day `d` entirely and record both sessions back to back the next morning, one challenge per video. Every session still gets done, so it isn't an escape hatch, but it lets a day slide. Shorter grace limits it.

Ways funds could get stuck:
- Blacklisted owner or beneficiary (§2 table). The brief forbids redirecting payouts, so these credits wait until Circle unblacklists. I've kept that behaviour.
- Lost owner key strands owner credits and the final sweep. Mitigation is off-chain: seed phrase backup for the hardware wallet.
- Non-USDC tokens sent by mistake are stuck forever unless sweep accepts other tokens. **[DECISION]** below.

No path found that strands funds when both addresses are healthy and the owner key exists. Every active day reaches a terminal state through `forfeit`, which anyone can call, so `sweep` is always eventually reachable.

## 9. Open decisions

From the brief:
1. Stake size and number of days (`trancheAmount`, `numDays`). Max 256 days per contract.
2. Schedule bitmap, and which days map to PT-A, PT-B, Walk-A, rest. The contract only stores active/rest. The session-type mapping lives in the verifier's config.
3. Beneficiary address.
4. Grace window. You leaned 18h. My recommendation: pick the day boundary and grace together so the deadline lands at a time you'd actually notice, e.g. boundary 04:00 local and grace 6h gives a 10:00 next-morning deadline and only a 6h overlap.
5. Day boundary hour in UTC. Constrained to a whole hour by the constructor.
6. Video retention period (verifier only, doesn't affect the contract).

Raised while writing this spec:
7. **Funder reclaim.** The brief says the funder reclaims a partial deposit if the contract isn't funded by `startTime`. But `fund()` is atomic: it pulls the full amount or nothing, so the only way a partial balance exists is a direct transfer, and then there's no recorded funder. I propose `reclaimUnfunded()` sends the whole balance to `owner`. That drops the third payout address entirely, so invariant 8 holds with no exception. Confirm?
8. **On-chain score floor.** Proposed: none, the verifier gates it (§6.1).
9. **Sweep scope.** Proposed: USDC only. Supporting arbitrary tokens means calling an arbitrary contract's `transfer`. It's safe behind `nonReentrant` with no state dependency, but it's code you didn't ask for. Say if you want it.
10. **`acceptVerifier` caller.** Proposed: owner only. Making it permissionless after the timelock would save one hardware-wallet signature and change nothing about safety, since the outcome is fixed by the proposal.
11. **`MAX_GRACE`.** Proposed: `< 86400`. A lower hard cap (e.g. 12h) would make the overlap in §8 a contract property instead of a deploy-time choice.

Things I'd propose but haven't built (per "propose, don't build"):
- `withdrawFor(account)`: anyone can push credits to `owner` or `beneficiary`. Same destinations, so the key property holds, and the relayer could push your credits without the hardware wallet signing. Needs an exception to "sends the caller's balance".
- `usedVideoHash` mapping to reject reusing one video for two days. Words already make this fail at the verifier, so it's belt-and-braces.

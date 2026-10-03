# Deploy checklist

One contract per commitment. Nothing is at risk until `fund()`; everything before it can be redone.

## Once, before the first deploy

- [ ] Rebuild with native solc 0.8.37 on your machine (`forge build --force && forge test`), all green.
- [ ] Verify both USDC addresses in `script/Deploy.s.sol` against https://developers.circle.com/stablecoins/usdc-contract-addresses (Base `0x8335…2913`, Base Sepolia `0x036C…CF7e`). Update the comments to say "verified <date>".
- [ ] Run the fork test: `BASE_RPC_URL=<rpc> forge test --mc BaseUSDCForkTest`.
- [ ] Owner is a hardware-wallet address with its seed backed up offline. It signs only `fund`, `withdraw`, `sweep`, and verifier rotation.
- [ ] Verifier key generated on the NUC and stored encrypted (DPAPI or keystore). Note its address.
- [ ] Relayer wallet holds only gas ETH. It is not the owner or verifier.
- [ ] No private keys in the repo or in `.env`. Sign with `--ledger` or `--account <keystore>`.

## Parameters

- [ ] `python3 script/start_time.py start --tz <IANA zone> --date <day 0> --hour 4 --anchor standard|daylight`. Read the printed local times for both seasons.
- [ ] `python3 script/start_time.py bitmap <pattern>` for `NUM_DAYS` and `SCHEDULE_BITMAP`. Keep the same pattern for the verifier's config.
- [ ] `TRANCHE_AMOUNT` in USDC base units (6 decimals: `10e6` = 10 USDC).
- [ ] `GRACE_SECONDS=21600` (6h). The contract caps it at 12h.
- [ ] `BENEFICIARY` is someone you'd genuinely rather not pay, and not an address you control.

## Each deploy (Anvil → Base Sepolia → mainnet small stake for two weeks → mainnet real amount)

1. [ ] Dry run: `forge script script/Deploy.s.sol --rpc-url <rpc>`. No `--broadcast`.
2. [ ] Read every printed line. Check `owner`, `beneficiary`, `verifier` character by character against their source, `totalRequired` against what you meant to lock, and `startTime` with `script/start_time.py show <ts> --tz <zone>`.
3. [ ] Copy the `PARAMS_HASH` value by hand.
4. [ ] Broadcast: `CONFIRM_PARAMS_HASH=<hash> forge script script/Deploy.s.sol --rpc-url <rpc> --broadcast --ledger --verify`.
5. [ ] On Basescan: source verified, and read every immutable (`owner`, `beneficiary`, `verifier`, `token`, `startTime`, `numDays`, `scheduleBitmap`, `trancheAmount`, `graceSeconds`, `totalRequired`) against step 2.
6. [ ] From the owner wallet: `approve(contract, totalRequired)` on USDC, then `fund()`, before `startTime`. Confirm `funded() == true` and the contract's USDC balance equals `totalRequired`.
7. [ ] Point the verifier and relayer config at the new address. Confirm the relayer's `seedDay` job fires on day 0.

## If something goes wrong

| Situation | Action |
| --- | --- |
| Wrong parameter, not yet funded | Don't fund. Deploy a new one. If tokens were sent by mistake, call `reclaimUnfunded()` after `startTime` (pays owner). |
| Verifier key lost or leaked | `proposeVerifier(new)` from owner, `acceptVerifier()` 48h later. Days whose window closes in between forfeit. |
| NUC down | Missed days forfeit. Nothing to do on chain. |
| All days resolved | `withdraw()` from owner and beneficiary; `sweep()` from owner if anything extra was sent. |

#!/usr/bin/env bash
# Full-lifecycle rehearsal on a local Anvil chain, using the real deploy script and only the calls the
# relayer, verifier, owner, and beneficiary would make in production. Every step checks its outcome and
# the script exits non-zero on the first surprise.
#
#   script/rehearse_anvil.sh [log-file]
#
# Schedule: 4 days "ABRA" (day 2 is rest). Day 0 claimed, day 1 missed and forfeited, verifier rotated
# between, day 3 seeded late and claimed at the exact deadline with the new key. Then a stray donation,
# sweep, and both withdrawals.
#
# Anvil's well-known dev keys are used below. They are public; never use them anywhere else.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG=${1:-/dev/null}
PORT=${ANVIL_PORT:-8545}
RPC=http://127.0.0.1:$PORT
TZ_NAME=${TZ_NAME:-America/Chicago}

# Anvil's default accounts, derived from its public test mnemonic.
MNEMONIC="test test test test test test test test test test test junk"
key() { cast wallet private-key --mnemonic "$MNEMONIC" --mnemonic-index "$1"; }
DEPLOYER_PK=$(key 0)
OWNER_PK=$(key 1)
BENEF_PK=$(key 2)
VERIFIER_PK=$(key 3)
RELAYER_PK=$(key 4)
NEW_VERIFIER_PK=$(key 5)
OWNER=$(cast wallet address $OWNER_PK)
BENEF=$(cast wallet address $BENEF_PK)
VERIFIER=$(cast wallet address $VERIFIER_PK)
NEW_VERIFIER=$(cast wallet address $NEW_VERIFIER_PK)

say() { echo "$*" | tee -a "$LOG"; }
fail() { say "FAIL: $*"; exit 1; }
num() { cast to-dec "$(echo "$1" | awk '{print $1}')"; }
expect_eq() { [ "$1" == "$2" ] || fail "$3: expected $2, got $1"; say "  ok  $3 = $1"; }
call() { cast call --rpc-url $RPC "$@"; }
send() { local pk=$1; shift; cast send --rpc-url $RPC --private-key "$pk" "$@" >/dev/null; }
expect_revert() { # expect_revert <errorSig> <pk> <args...>
  local sig=$1 pk=$2; shift 2
  local sel; sel=$(cast sig "$sig")
  local out
  if out=$(cast send --rpc-url $RPC --private-key "$pk" "$@" 2>&1); then fail "expected revert $sig, call succeeded"; fi
  echo "$out" | grep -q -e "$sel" -e "${sig%%(*}" || fail "expected $sig ($sel), got: $(echo "$out" | tail -2)"
  say "  ok  reverted ${sig%%(*}"
}
set_time() { cast rpc --rpc-url $RPC evm_setNextBlockTimestamp "$1" >/dev/null; cast rpc --rpc-url $RPC evm_mine >/dev/null; }
next_time() { cast rpc --rpc-url $RPC evm_setNextBlockTimestamp "$1" >/dev/null; } # applies to the next tx
now() { cast block --rpc-url $RPC latest --field timestamp; }

anvil --port "$PORT" --silent &
ANVIL_PID=$!
trap 'kill $ANVIL_PID 2>/dev/null || true' EXIT
for _ in $(seq 50); do cast chain-id --rpc-url $RPC >/dev/null 2>&1 && break; sleep 0.2; done

say "== 1. Mock USDC"
USDC=$(forge create --rpc-url $RPC --private-key $DEPLOYER_PK --broadcast test/mocks/MockUSDC.sol:MockUSDC 2>/dev/null \
  | awk '/Deployed to:/{print $3}')
[ -n "$USDC" ] || fail "MockUSDC deploy"
say "  USDC $USDC"

say "== 2. Parameters (time zone $TZ_NAME, 04:00 boundary, standard-time anchor)"
TOMORROW=$(python3 -c "import datetime,sys;from zoneinfo import ZoneInfo;print((datetime.datetime.fromtimestamp($(now),ZoneInfo('$TZ_NAME'))+datetime.timedelta(days=1)).date())")
START_TIME=$(python3 script/start_time.py start --tz "$TZ_NAME" --date "$TOMORROW" --hour 4 --anchor standard | head -1)
eval "$(python3 script/start_time.py bitmap ABRA | grep -E '^(NUM_DAYS|SCHEDULE_BITMAP)=')"
python3 script/start_time.py show "$START_TIME" --tz "$TZ_NAME" | tee -a "$LOG"
export OWNER BENEFICIARY=$BENEF VERIFIER TOKEN=$USDC START_TIME NUM_DAYS SCHEDULE_BITMAP TRANCHE_AMOUNT=25000000 GRACE_SECONDS=21600

say "== 3. Deploy: dry run, then broadcast with the confirmed hash"
DRY=$(forge script script/Deploy.s.sol --rpc-url $RPC 2>&1)
echo "$DRY" | grep -E "^\s+(chainId|owner|beneficiary|verifier|token|startTime|numDays|active|tranche|total|grace|PARAMS)" | tee -a "$LOG"
HASH=$(echo "$DRY" | grep "PARAMS_HASH " | grep -oE "0x[0-9a-f]{64}")
WRONG=0x$(printf '%063d' 0)1
if CONFIRM_PARAMS_HASH=$WRONG forge script script/Deploy.s.sol --rpc-url $RPC --broadcast --private-key $DEPLOYER_PK >/dev/null 2>&1; then
  fail "broadcast with wrong hash went through"
fi
say "  ok  wrong hash refused"
OUT=$(CONFIRM_PARAMS_HASH=$HASH forge script script/Deploy.s.sol --rpc-url $RPC --broadcast --private-key $DEPLOYER_PK 2>&1)
C=$(echo "$OUT" | awk '/deployed /{print $2}' | tail -1)
[ -n "$C" ] || fail "deploy: $(echo "$OUT" | tail -5)"
say "  PTCommitment $C"
expect_eq "$(call $C 'owner()(address)')" "$OWNER" "owner"
expect_eq "$(call $C 'beneficiary()(address)')" "$BENEF" "beneficiary"
expect_eq "$(num "$(call $C 'totalRequired()(uint256)')")" "75000000" "totalRequired (3 active x 25 USDC)"

say "== 4. Fund from owner"
expect_revert "NotFunded()" $RELAYER_PK $C "seedDay(uint256)" 0
send $DEPLOYER_PK $USDC "mint(address,uint256)" $OWNER 75000000
send $OWNER_PK $USDC "approve(address,uint256)" $C 75000000
send $OWNER_PK $C "fund()"
expect_eq "$(call $C 'funded()(bool)')" "true" "funded"
expect_eq "$(num "$(call $USDC 'balanceOf(address)(uint256)' $C)")" "75000000" "contract balance"
expect_revert "AlreadyFunded()" $OWNER_PK $C "fund()"

day_start() { num "$(call $C 'dayStart(uint256)(uint256)' "$1")"; }
deadline() { num "$(call $C 'claimDeadline(uint256)(uint256)' "$1")"; }
sign_claim() { # sign_claim <day> <videoHash> <score> <pk>
  local digest; digest=$(call $C "claimDigest(uint256,bytes32,uint32)(bytes32)" "$1" "$2" "$3")
  cast wallet sign --no-hash "$digest" --private-key "$4"
}
VIDEO=$(cast keccak "day-video")

say "== 5. Day 0: seed at 04:05 local, claim from the relayer"
expect_revert "DayNotStarted(uint256)" $RELAYER_PK $C "seedDay(uint256)" 0
set_time $(( $(day_start 0) + 300 ))
send $RELAYER_PK $C "seedDay(uint256)" 0
expect_revert "AlreadySeeded(uint256)" $RELAYER_PK $C "seedDay(uint256)" 0
SIG=$(sign_claim 0 $VIDEO 93 $VERIFIER_PK)
BAD=$(sign_claim 0 $VIDEO 93 $NEW_VERIFIER_PK)
expect_revert "InvalidSignature()" $RELAYER_PK $C "claim(uint256,bytes32,uint32,bytes)" 0 $VIDEO 93 $BAD
expect_revert "InvalidSignature()" $RELAYER_PK $C "claim(uint256,bytes32,uint32,bytes)" 0 $VIDEO 94 $SIG
send $RELAYER_PK $C "claim(uint256,bytes32,uint32,bytes)" 0 $VIDEO 93 $SIG
expect_eq "$(call $C 'dayState(uint256)(uint8)' 0)" "2" "day 0 state Claimed"
expect_eq "$(num "$(call $C 'credit(address)(uint256)' $OWNER)")" "25000000" "owner credit"

say "== 6. Verifier rotation proposed (key-loss drill)"
send $OWNER_PK $C "proposeVerifier(address)" $NEW_VERIFIER
expect_revert "VerifierTimelocked(uint256)" $OWNER_PK $C "acceptVerifier()"
expect_revert "NotOwner()" $RELAYER_PK $C "proposeVerifier(address)" $NEW_VERIFIER

say "== 7. Day 1: missed. Forfeit refused at the deadline, accepted one second later"
set_time $(( $(day_start 1) + 300 ))
send $RELAYER_PK $C "seedDay(uint256)" 1
next_time "$(deadline 1)"
expect_revert "ClaimWindowOpen(uint256)" $BENEF_PK $C "forfeit(uint256)" 1
set_time $(( $(deadline 1) + 1 ))
expect_eq "$(now)" "$(( $(deadline 1) + 1 ))" "chain time == claimDeadline(1) + 1"
send $BENEF_PK $C "forfeit(uint256)" 1
expect_eq "$(call $C 'dayState(uint256)(uint8)' 1)" "3" "day 1 state Forfeited"
expect_eq "$(num "$(call $C 'credit(address)(uint256)' $BENEF)")" "25000000" "beneficiary credit"

say "== 8. Day 2: rest day"
expect_revert "InactiveDay(uint256)" $RELAYER_PK $C "seedDay(uint256)" 2

say "== 9. Rotation accepted after 48h; old key now refused"
set_time "$(day_start 3)"
send $OWNER_PK $C "acceptVerifier()"
expect_eq "$(call $C 'verifier()(address)')" "$NEW_VERIFIER" "verifier rotated"

say "== 10. Day 3: seeded 5h into grace, claimed at the exact deadline with the new key"
set_time $(( $(day_start 3) + 86400 + 5 * 3600 ))
send $RELAYER_PK $C "seedDay(uint256)" 3
OLD=$(sign_claim 3 $VIDEO 88 $VERIFIER_PK)
NEW=$(sign_claim 3 $VIDEO 88 $NEW_VERIFIER_PK)
expect_revert "InvalidSignature()" $RELAYER_PK $C "claim(uint256,bytes32,uint32,bytes)" 3 $VIDEO 88 $OLD
next_time "$(deadline 3)"
send $RELAYER_PK $C "claim(uint256,bytes32,uint32,bytes)" 3 $VIDEO 88 $NEW
expect_eq "$(now)" "$(deadline 3)" "claim block timestamp == claimDeadline(3)"
expect_eq "$(call $C 'unresolvedDays()(uint256)')" "0" "unresolved days"

say "== 11. Stray donation, sweep, withdrawals"
send $DEPLOYER_PK $USDC "mint(address,uint256)" $C 1234567
expect_revert "NotOwner()" $BENEF_PK $C "sweep()"
send $OWNER_PK $C "sweep()"
expect_revert "NothingToSweep()" $OWNER_PK $C "sweep()"
expect_revert "NotPayee()" $RELAYER_PK $C "withdraw()"
send $OWNER_PK $C "withdraw()"
send $BENEF_PK $C "withdraw()"
expect_revert "NothingToWithdraw()" $OWNER_PK $C "withdraw()"
expect_eq "$(num "$(call $USDC 'balanceOf(address)(uint256)' $OWNER)")" "51234567" "owner USDC (2 tranches + swept 1.234567)"
expect_eq "$(num "$(call $USDC 'balanceOf(address)(uint256)' $BENEF)")" "25000000" "beneficiary USDC (1 tranche)"
expect_eq "$(num "$(call $USDC 'balanceOf(address)(uint256)' $C)")" "0" "contract USDC"

say "== 12. Contract rejects ETH"
if cast send --rpc-url $RPC --private-key $RELAYER_PK $C --value 1ether >/dev/null 2>&1; then fail "contract accepted ETH"; fi
say "  ok  ETH send reverted"

say "REHEARSAL PASSED"

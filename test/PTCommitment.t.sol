// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Errors} from "@openzeppelin/contracts/interfaces/draft-IERC6093.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {stdError} from "forge-std/StdError.sol";
import {PTCommitment} from "../src/PTCommitment.sol";
import {MockUSDC, ReentrantToken} from "./mocks/MockUSDC.sol";
import {Base} from "./Base.t.sol";

contract ConstructorTest is Base {
    function _make(
        address o,
        address b,
        address v,
        address t,
        uint256 st,
        uint256 n,
        uint256 bm,
        uint256 tr,
        uint256 g
    ) internal returns (PTCommitment) {
        return new PTCommitment(o, b, v, IERC20(t), st, n, bm, tr, g);
    }

    function test_setsImmutables() public view {
        assertEq(c.owner(), owner);
        assertEq(c.beneficiary(), beneficiary);
        assertEq(c.verifier(), verifierAddr);
        assertEq(address(c.token()), address(usdc));
        assertEq(c.startTime(), start);
        assertEq(c.numDays(), NUM_DAYS);
        assertEq(c.scheduleBitmap(), BITMAP);
        assertEq(c.trancheAmount(), TRANCHE);
        assertEq(c.graceSeconds(), GRACE);
        assertEq(c.activeDayCount(), ACTIVE);
        assertEq(c.totalRequired(), TRANCHE * ACTIVE);
        assertEq(c.unresolvedDays(), ACTIVE);
        assertFalse(c.funded());
        assertEq(c.pendingVerifier(), address(0));
    }

    function test_revert_zeroAddresses() public {
        vm.expectRevert(PTCommitment.ZeroAddress.selector);
        _make(address(0), beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        vm.expectRevert(PTCommitment.ZeroAddress.selector);
        _make(owner, address(0), verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        vm.expectRevert(PTCommitment.ZeroAddress.selector);
        _make(owner, beneficiary, address(0), address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_beneficiaryIsOwner() public {
        vm.expectRevert(PTCommitment.InvalidPayee.selector);
        _make(owner, owner, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_payeeIsContractItself() public {
        address next = vm.computeCreateAddress(address(this), vm.getNonce(address(this)));
        vm.expectRevert(PTCommitment.InvalidPayee.selector);
        _make(next, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        next = vm.computeCreateAddress(address(this), vm.getNonce(address(this)));
        vm.expectRevert(PTCommitment.InvalidPayee.selector);
        _make(owner, next, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_tokenWithoutCode() public {
        vm.expectRevert(PTCommitment.TokenHasNoCode.selector);
        _make(owner, beneficiary, verifierAddr, address(0), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        vm.expectRevert(PTCommitment.TokenHasNoCode.selector);
        _make(owner, beneficiary, verifierAddr, stranger, start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_startTime() public {
        uint256 nowHour = block.timestamp - (block.timestamp % 1 hours);
        // in the past / now
        vm.expectRevert(PTCommitment.InvalidStartTime.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), nowHour, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        // not on the hour
        vm.expectRevert(PTCommitment.InvalidStartTime.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start + 1, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        // a millisecond timestamp typo: too far out
        vm.expectRevert(PTCommitment.InvalidStartTime.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start * 1000, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_startTime_bounds() public {
        uint256 maxStart = block.timestamp + 365 days;
        maxStart -= maxStart % 1 hours;
        _make(owner, beneficiary, verifierAddr, address(usdc), maxStart, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        vm.warp(start - 1);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        vm.warp(start);
        vm.expectRevert(PTCommitment.InvalidStartTime.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_numDays() public {
        vm.expectRevert(PTCommitment.InvalidNumDays.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, 0, BITMAP, TRANCHE, GRACE);
        vm.expectRevert(PTCommitment.InvalidNumDays.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, 257, BITMAP, TRANCHE, GRACE);
    }

    function test_revert_schedule() public {
        vm.expectRevert(PTCommitment.InvalidSchedule.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, 0, TRANCHE, GRACE);
        vm.expectRevert(PTCommitment.InvalidSchedule.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP | (1 << NUM_DAYS), TRANCHE, GRACE);
    }

    function test_fullSchedule256() public {
        PTCommitment full =
            _make(owner, beneficiary, verifierAddr, address(usdc), start, 256, type(uint256).max, TRANCHE, GRACE);
        assertEq(full.activeDayCount(), 256);
        assertEq(full.totalRequired(), TRANCHE * 256);
        assertTrue(full.isActive(255));
        assertFalse(full.isActive(256));
    }

    function test_revert_zeroTranche() public {
        vm.expectRevert(PTCommitment.ZeroTranche.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, 0, GRACE);
    }

    function test_grace_bounds() public {
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, 0);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, 12 hours);
        vm.expectRevert(PTCommitment.GraceTooLong.selector);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, 12 hours + 1);
    }

    function test_revert_totalRequiredOverflow() public {
        vm.expectRevert(stdError.arithmeticError);
        _make(owner, beneficiary, verifierAddr, address(usdc), start, NUM_DAYS, 0x3, type(uint256).max / 2 + 1, GRACE);
    }

    function test_verifierMayEqualOwner() public {
        PTCommitment p = _make(owner, beneficiary, owner, address(usdc), start, NUM_DAYS, BITMAP, TRANCHE, GRACE);
        assertEq(p.verifier(), owner);
    }
}

contract FundingTest is Base {
    function test_fund() public {
        uint256 amount = c.totalRequired();
        usdc.mint(owner, amount);
        vm.startPrank(owner);
        usdc.approve(address(c), amount);
        vm.expectEmit(address(c));
        emit PTCommitment.Funded(owner, amount);
        c.fund();
        vm.stopPrank();
        assertTrue(c.funded());
        assertEq(usdc.balanceOf(address(c)), amount);
        assertEq(usdc.balanceOf(owner), 0);
    }

    function test_fund_byThirdPartyGivesNoRights() public {
        uint256 amount = c.totalRequired();
        usdc.mint(stranger, amount);
        vm.startPrank(stranger);
        usdc.approve(address(c), amount);
        c.fund();
        vm.expectRevert(PTCommitment.NotPayee.selector);
        c.withdraw();
        vm.expectRevert(PTCommitment.NotOwner.selector);
        c.sweep();
        vm.stopPrank();
        assertEq(c.credit(stranger), 0);
    }

    function test_fund_lastSecond() public {
        vm.warp(start - 1);
        _fund();
        assertTrue(c.funded());
    }

    function test_revert_fundTwice() public {
        _fund();
        vm.expectRevert(PTCommitment.AlreadyFunded.selector);
        c.fund();
    }

    function test_revert_fundAtStart() public {
        vm.warp(start);
        vm.expectRevert(PTCommitment.FundingClosed.selector);
        c.fund();
    }

    function test_revert_fundWithoutAllowance() public {
        usdc.mint(owner, c.totalRequired());
        vm.prank(owner);
        vm.expectRevert(
            abi.encodeWithSelector(IERC20Errors.ERC20InsufficientAllowance.selector, address(c), 0, c.totalRequired())
        );
        c.fund();
        assertFalse(c.funded());
    }

    function test_revert_fundFeeOnTransfer() public {
        usdc.setFeeBps(1);
        uint256 amount = c.totalRequired();
        usdc.mint(owner, amount);
        vm.startPrank(owner);
        usdc.approve(address(c), amount);
        vm.expectRevert(PTCommitment.FundingAmountMismatch.selector);
        c.fund();
        vm.stopPrank();
        assertFalse(c.funded());
    }

    function test_directTransferDoesNotActivate() public {
        usdc.mint(address(c), c.totalRequired() * 2);
        assertFalse(c.funded());
        _warpTo(start);
        vm.expectRevert(PTCommitment.NotFunded.selector);
        c.seedDay(0);
    }

    function test_reclaimUnfunded() public {
        usdc.mint(address(c), 123);
        _warpTo(start);
        vm.expectEmit(address(c));
        emit PTCommitment.UnfundedReclaimed(123);
        vm.prank(stranger);
        c.reclaimUnfunded();
        assertEq(usdc.balanceOf(owner), 123);
        assertEq(usdc.balanceOf(address(c)), 0);
    }

    function test_revert_reclaimWhenFunded() public {
        _fund();
        _warpTo(start);
        vm.expectRevert(PTCommitment.AlreadyFunded.selector);
        c.reclaimUnfunded();
    }

    function test_revert_reclaimBeforeStart() public {
        usdc.mint(address(c), 1);
        vm.warp(start - 1);
        vm.expectRevert(PTCommitment.NotDead.selector);
        c.reclaimUnfunded();
    }

    function test_revert_reclaimNothing() public {
        _warpTo(start);
        vm.expectRevert(PTCommitment.NothingToReclaim.selector);
        c.reclaimUnfunded();
    }

    function test_deadContractRejectsFunding() public {
        _warpTo(start);
        vm.expectRevert(PTCommitment.FundingClosed.selector);
        c.fund();
    }
}

contract SeedTest is Base {
    function setUp() public override {
        super.setUp();
        _fund();
    }

    function test_seed_storesChallenge() public {
        _warpTo(c.dayStart(0));
        bytes32 prev = keccak256("prev block");
        vm.setBlockhash(block.number - 1, prev);
        bytes32 expected = keccak256(abi.encode(prev, uint256(0), address(c)));
        vm.expectEmit(address(c));
        emit PTCommitment.DaySeeded(0, expected);
        vm.prank(stranger);
        c.seedDay(0);
        assertEq(c.challenge(0), expected);
        assertEq(uint8(c.dayState(0)), uint8(PTCommitment.DayState.Seeded));
    }

    /// blockhash only reaches back 256 blocks, so the challenge could not be recomputed later.
    /// That is why it is stored. A claim still works long after the seeding block has aged out.
    function test_challengeOutlivesBlockhashWindow() public {
        _warpTo(c.dayStart(0));
        uint256 seedBlock = block.number - 1;
        vm.setBlockhash(seedBlock, keccak256("h"));
        c.seedDay(0);
        bytes32 stored = c.challenge(0);
        assertEq(stored, keccak256(abi.encode(keccak256("h"), uint256(0), address(c))));

        vm.roll(block.number + 300);
        vm.warp(c.dayStart(0) + 20 hours);
        assertEq(blockhash(seedBlock), bytes32(0), "blockhash should be out of range");
        assertEq(c.challenge(0), stored);
        _claim(0);
        assertEq(uint8(c.dayState(0)), uint8(PTCommitment.DayState.Claimed));
    }

    function test_seed_windowEdges() public {
        // at dayStart: ok
        _seedAt(0, c.dayStart(0));
        // at claimDeadline: ok
        _seedAt(1, c.claimDeadline(1));
        // deadline + 1
        _warpTo(c.claimDeadline(2) + 1);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.ClaimWindowClosed.selector, 2));
        c.seedDay(2);
        // day 4 one second before it starts
        _warpTo(c.dayStart(4) - 1);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.DayNotStarted.selector, 4));
        c.seedDay(4);
    }

    function test_revert_seedBeforeStart() public {
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.DayNotStarted.selector, 0));
        c.seedDay(0);
    }

    function test_revert_seedTwice() public {
        _seed(0);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadySeeded.selector, 0));
        c.seedDay(0);
    }

    function test_revert_seedResolvedDay() public {
        _seed(0);
        _claim(0);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadySeeded.selector, 0));
        c.seedDay(0);
    }

    function test_revert_seedRestDay() public {
        _warpTo(c.dayStart(REST_DAY));
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, REST_DAY));
        c.seedDay(REST_DAY);
    }

    function test_revert_seedOutOfRange() public {
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, NUM_DAYS));
        c.seedDay(NUM_DAYS);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, type(uint256).max));
        c.seedDay(type(uint256).max);
    }

    function test_revert_seedUnfunded() public {
        PTCommitment fresh = _deploy();
        _warpTo(start);
        vm.expectRevert(PTCommitment.NotFunded.selector);
        fresh.seedDay(0);
    }
}

contract ClaimTest is Base {
    function setUp() public override {
        super.setUp();
        _fund();
    }

    function test_claim() public {
        _seed(0);
        bytes memory sig = _sign(0);
        vm.expectEmit(address(c));
        emit PTCommitment.DayClaimed(0, VIDEO, SCORE);
        vm.prank(relayer);
        c.claim(0, VIDEO, SCORE, sig);
        assertEq(uint8(c.dayState(0)), uint8(PTCommitment.DayState.Claimed));
        assertEq(c.credit(owner), TRANCHE);
        assertEq(c.credit(relayer), 0);
        assertEq(c.totalCredited(), TRANCHE);
        assertEq(c.unresolvedDays(), ACTIVE - 1);
    }

    function test_claim_windowEdges() public {
        // claim in the same second the day is seeded
        _seed(0);
        _claim(0);
        // claim at the exact deadline
        _seed(1);
        bytes memory sig = _sign(1);
        _seed(2);
        bytes memory sig2 = _sign(2);
        _warpTo(c.claimDeadline(1));
        c.claim(1, VIDEO, SCORE, sig);
        // deadline + 1 fails
        sig = sig2;
        _warpTo(c.claimDeadline(2) + 1);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.ClaimWindowClosed.selector, 2));
        c.claim(2, VIDEO, SCORE, sig);
    }

    function test_claim_duringGraceOfPreviousDay() public {
        // With grace, day d and d+1 windows overlap; both claimable in the overlap.
        _seed(0);
        bytes memory sig0 = _sign(0);
        _seed(1);
        bytes memory sig1 = _sign(1);
        _warpTo(c.dayEnd(0) + GRACE);
        c.claim(0, VIDEO, SCORE, sig0);
        c.claim(1, VIDEO, SCORE, sig1);
        assertEq(c.credit(owner), 2 * TRANCHE);
    }

    function test_revert_claimUnseeded() public {
        _warpTo(c.dayStart(0));
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.NotSeeded.selector, 0));
        c.claim(0, VIDEO, SCORE, "");
    }

    function test_revert_claimTwice() public {
        _seed(0);
        bytes memory sig = _sign(0);
        c.claim(0, VIDEO, SCORE, sig);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadyResolved.selector, 0));
        c.claim(0, VIDEO, SCORE, sig);
    }

    function test_revert_claimForfeited() public {
        _seed(0);
        bytes memory sig = _sign(0);
        _warpTo(c.claimDeadline(0) + 1);
        c.forfeit(0);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadyResolved.selector, 0));
        c.claim(0, VIDEO, SCORE, sig);
    }

    function test_revert_claimRestDay() public {
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, REST_DAY));
        c.claim(REST_DAY, VIDEO, SCORE, "");
    }

    function test_revert_claimUnfunded() public {
        PTCommitment fresh = _deploy();
        vm.expectRevert(PTCommitment.NotFunded.selector);
        fresh.claim(0, VIDEO, SCORE, "");
    }

    function test_revert_badSignatureLength() public {
        _seed(0);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, hex"1234");
    }

    function test_revert_tamperedFields() public {
        _seed(0);
        bytes memory sig = _sign(0);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, keccak256("other video"), SCORE, sig);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE + 1, sig);
    }

    function test_frontRunClaimStillPaysOwner() public {
        _seed(0);
        bytes memory sig = _sign(0);
        vm.prank(stranger);
        c.claim(0, VIDEO, SCORE, sig);
        assertEq(c.credit(owner), TRANCHE);
        assertEq(c.credit(stranger), 0);
        vm.prank(relayer);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadyResolved.selector, 0));
        c.claim(0, VIDEO, SCORE, sig);
    }
}

contract SignatureTest is Base {
    uint256 internal constant SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;

    function setUp() public override {
        super.setUp();
        _fund();
    }

    function test_digestMatchesSpec() public {
        _seed(0);
        assertEq(c.claimDigest(0, VIDEO, SCORE), _specDigest(c, 0, c.challenge(0), VIDEO, SCORE));
    }

    function test_revert_replayOnAnotherDay() public {
        _seed(0);
        bytes memory sig0 = _sign(0);
        c.claim(0, VIDEO, SCORE, sig0);
        _seed(1);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(1, VIDEO, SCORE, sig0);
    }

    function test_revert_replayOnAnotherDeployment() public {
        PTCommitment other = _deploy();
        _fund(other);
        _warpTo(start);
        c.seedDay(0);
        other.seedDay(0);
        bytes memory sigOther = _sign(other, verifierPk, 0, VIDEO, SCORE);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sigOther);
        other.claim(0, VIDEO, SCORE, sigOther);
    }

    function test_revert_replayOnAnotherDeployment_sameChallenge() public {
        // Even if two deployments somehow stored the same challenge, verifyingContract in the domain separates them.
        PTCommitment other = _deploy();
        _fund(other);
        _seed(0);
        _warpTo(block.timestamp);
        other.seedDay(0);
        vm.store(address(other), _challengeSlot(0), c.challenge(0));
        assertEq(other.challenge(0), c.challenge(0));
        bytes memory sigOther = _sign(other, verifierPk, 0, VIDEO, SCORE);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sigOther);
    }

    function test_revert_replayOnAnotherChainId() public {
        _seed(0);
        bytes memory sig = _sign(0);
        uint256 original = block.chainid;
        vm.chainId(original + 1);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sig);
        vm.chainId(original);
        c.claim(0, VIDEO, SCORE, sig);
    }

    function test_revert_wrongSigner() public {
        _seed(0);
        bytes memory sig = _sign(c, 0xBAD, 0, VIDEO, SCORE);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sig);
    }

    function test_revert_malleableS() public {
        _seed(0);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(verifierPk, c.claimDigest(0, VIDEO, SCORE));
        bytes32 highS = bytes32(SECP256K1_N - uint256(s));
        uint8 flippedV = v == 27 ? 28 : 27;
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, abi.encodePacked(r, highS, flippedV));
        c.claim(0, VIDEO, SCORE, abi.encodePacked(r, s, v));
    }

    function test_revert_badV() public {
        _seed(0);
        (, bytes32 r, bytes32 s) = vm.sign(verifierPk, c.claimDigest(0, VIDEO, SCORE));
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, abi.encodePacked(r, s, uint8(29)));
    }

    function test_revert_zeroSignature() public {
        _seed(0);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, new bytes(65));
    }

    /// A signature made over any challenge other than the stored one fails: one computed before
    /// seeding (challenge 0), and one over a guessed challenge.
    function test_revert_staleChallenge() public {
        _warpTo(c.dayStart(0));
        bytes memory preSeed = _sign(0); // challenge[0] == 0 at this point
        bytes32 guess = keccak256("guess");
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(verifierPk, _specDigest(c, 0, guess, VIDEO, SCORE));
        c.seedDay(0);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, preSeed);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, abi.encodePacked(r, s, v));
    }

    /// @dev `challenge` mapping base slot, from `forge inspect PTCommitment storageLayout`.
    ///      test_challengeSlotLayout guards it.
    uint256 internal constant _CHALLENGE_SLOT = 8;

    function _challengeSlot(uint256 d) internal pure returns (bytes32) {
        return keccak256(abi.encode(d, _CHALLENGE_SLOT));
    }

    function test_challengeSlotLayout() public {
        _seed(0);
        assertEq(vm.load(address(c), _challengeSlot(0)), c.challenge(0));
    }
}

contract ForfeitTest is Base {
    function setUp() public override {
        super.setUp();
        _fund();
    }

    function test_forfeitUnseeded() public {
        _warpTo(c.claimDeadline(0) + 1);
        vm.expectEmit(address(c));
        emit PTCommitment.DayForfeited(0);
        vm.prank(stranger);
        c.forfeit(0);
        assertEq(uint8(c.dayState(0)), uint8(PTCommitment.DayState.Forfeited));
        assertEq(c.credit(beneficiary), TRANCHE);
        assertEq(c.credit(stranger), 0);
        assertEq(c.unresolvedDays(), ACTIVE - 1);
    }

    function test_forfeitSeeded() public {
        _seed(0);
        _warpTo(c.claimDeadline(0) + 1);
        c.forfeit(0);
        assertEq(c.credit(beneficiary), TRANCHE);
    }

    function test_revert_forfeitAtDeadline() public {
        _warpTo(c.claimDeadline(0));
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.ClaimWindowOpen.selector, 0));
        c.forfeit(0);
    }

    function test_revert_forfeitClaimed() public {
        _seed(0);
        _claim(0);
        _warpTo(c.claimDeadline(0) + 1);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadyResolved.selector, 0));
        c.forfeit(0);
    }

    function test_revert_forfeitTwice() public {
        _warpTo(c.claimDeadline(0) + 1);
        c.forfeit(0);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.AlreadyResolved.selector, 0));
        c.forfeit(0);
    }

    function test_revert_forfeitRestDay() public {
        _warpTo(c.claimDeadline(REST_DAY) + 1);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, REST_DAY));
        c.forfeit(REST_DAY);
    }

    function test_revert_forfeitUnfunded() public {
        PTCommitment fresh = _deploy();
        _warpTo(start + 30 days);
        vm.expectRevert(PTCommitment.NotFunded.selector);
        fresh.forfeit(0);
    }

    function test_forfeitMany_skipsResolvedAndDuplicates() public {
        _seed(0);
        _claim(0);
        _warpTo(c.claimDeadline(2) + 1);
        c.forfeit(1);
        uint256[] memory list = new uint256[](5);
        list[0] = 0; // claimed: skipped
        list[1] = 1; // already forfeited: skipped
        list[2] = 2;
        list[3] = 2; // duplicate: skipped
        list[4] = 1;
        c.forfeitMany(list);
        assertEq(c.credit(beneficiary), 2 * TRANCHE);
        assertEq(c.credit(owner), TRANCHE);
        assertEq(c.unresolvedDays(), ACTIVE - 3);
    }

    function test_forfeitMany_empty() public {
        c.forfeitMany(new uint256[](0));
        assertEq(c.unresolvedDays(), ACTIVE);
    }

    function test_revert_forfeitMany_openWindow() public {
        _warpTo(c.claimDeadline(0) + 1);
        uint256[] memory list = new uint256[](2);
        list[0] = 0;
        list[1] = 1;
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.ClaimWindowOpen.selector, 1));
        c.forfeitMany(list);
    }

    function test_revert_forfeitMany_restDay() public {
        _warpTo(c.claimDeadline(NUM_DAYS - 1) + 1);
        uint256[] memory list = new uint256[](1);
        list[0] = REST_DAY;
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, REST_DAY));
        c.forfeitMany(list);
    }

    function test_revert_forfeitMany_unfunded() public {
        PTCommitment fresh = _deploy();
        _warpTo(start + 30 days);
        uint256[] memory list = new uint256[](1);
        vm.expectRevert(PTCommitment.NotFunded.selector);
        fresh.forfeitMany(list);
    }

    function test_revert_forfeitMany_tooLarge() public {
        vm.expectRevert(PTCommitment.BatchTooLarge.selector);
        c.forfeitMany(new uint256[](257));
    }

    function test_forfeitMany_all() public {
        _warpTo(c.claimDeadline(NUM_DAYS - 1) + 1);
        c.forfeitMany(_activeDays());
        assertEq(c.unresolvedDays(), 0);
        assertEq(c.credit(beneficiary), TRANCHE * ACTIVE);
    }

    /// Worst case: 256 active days forfeited in one batch stays well under Base's block gas limit.
    function test_forfeitMany_256GasBound() public {
        PTCommitment full = new PTCommitment(
            owner, beneficiary, verifierAddr, IERC20(address(usdc)), start, 256, type(uint256).max, 1, GRACE
        );
        _fund(full);
        _warpTo(full.claimDeadline(255) + 1);
        uint256[] memory list = new uint256[](256);
        for (uint256 i; i < 256; ++i) {
            list[i] = i;
        }
        uint256 gasBefore = gasleft();
        full.forfeitMany(list);
        uint256 used = gasBefore - gasleft();
        assertEq(full.unresolvedDays(), 0);
        assertLt(used, 15_000_000);
        emit log_named_uint("gas for 256-day forfeitMany", used);
    }
}

contract WithdrawTest is Base {
    function setUp() public override {
        super.setUp();
        _fund();
    }

    function test_withdrawBoth() public {
        _seed(0);
        _claim(0);
        _warpTo(c.claimDeadline(1) + 1);
        c.forfeit(1);

        vm.expectEmit(address(c));
        emit PTCommitment.Withdrawn(owner, TRANCHE);
        vm.prank(owner);
        c.withdraw();
        vm.prank(beneficiary);
        c.withdraw();

        assertEq(usdc.balanceOf(owner), TRANCHE);
        assertEq(usdc.balanceOf(beneficiary), TRANCHE);
        assertEq(c.credit(owner), 0);
        assertEq(c.totalCredited(), 0);
        assertEq(usdc.balanceOf(address(c)), TRANCHE * (ACTIVE - 2));
    }

    function test_revert_withdrawNotPayee() public {
        vm.prank(stranger);
        vm.expectRevert(PTCommitment.NotPayee.selector);
        c.withdraw();
        vm.prank(verifierAddr);
        vm.expectRevert(PTCommitment.NotPayee.selector);
        c.withdraw();
    }

    function test_revert_withdrawNothing() public {
        vm.prank(owner);
        vm.expectRevert(PTCommitment.NothingToWithdraw.selector);
        c.withdraw();
        vm.prank(beneficiary);
        vm.expectRevert(PTCommitment.NothingToWithdraw.selector);
        c.withdraw();
    }

    function test_blacklistedBeneficiaryDoesNotBlockOwner() public {
        usdc.setBlacklisted(beneficiary, true);
        _warpTo(c.claimDeadline(0) + 1);
        c.forfeit(0);
        _seedAt(1, block.timestamp);
        _claim(1);
        vm.prank(beneficiary);
        vm.expectRevert(abi.encodeWithSelector(MockUSDC.Blacklisted.selector, beneficiary));
        c.withdraw();
        vm.prank(owner);
        c.withdraw();
        assertEq(usdc.balanceOf(owner), TRANCHE);
        assertEq(c.credit(beneficiary), TRANCHE, "credit kept for when unblacklisted");
        usdc.setBlacklisted(beneficiary, false);
        vm.prank(beneficiary);
        c.withdraw();
        assertEq(usdc.balanceOf(beneficiary), TRANCHE);
    }

    function test_blacklistedOwnerDoesNotBlockBeneficiary() public {
        usdc.setBlacklisted(owner, true);
        _seed(0);
        _claim(0);
        _warpTo(c.claimDeadline(1) + 1);
        c.forfeit(1);
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(MockUSDC.Blacklisted.selector, owner));
        c.withdraw();
        vm.prank(beneficiary);
        c.withdraw();
        assertEq(usdc.balanceOf(beneficiary), TRANCHE);
    }
}

contract SweepTest is Base {
    function setUp() public override {
        super.setUp();
        _fund();
    }

    function _resolveAll() internal {
        _seed(0);
        _claim(0);
        _warpTo(c.claimDeadline(NUM_DAYS - 1) + 1);
        c.forfeitMany(_activeDays());
    }

    function test_sweepExcessOnly() public {
        usdc.mint(address(c), 777);
        _resolveAll();
        vm.expectEmit(address(c));
        emit PTCommitment.Swept(777);
        vm.prank(owner);
        c.sweep();
        assertEq(usdc.balanceOf(owner), 777);
        assertEq(usdc.balanceOf(address(c)), c.totalCredited());
        vm.prank(beneficiary);
        c.withdraw();
        vm.prank(owner);
        c.withdraw();
        assertEq(usdc.balanceOf(address(c)), 0);
        assertEq(usdc.balanceOf(beneficiary), TRANCHE * (ACTIVE - 1));
    }

    function test_sweepAfterWithdrawals() public {
        _resolveAll();
        vm.prank(beneficiary);
        c.withdraw();
        usdc.mint(address(c), 5);
        vm.prank(owner);
        c.sweep();
        assertEq(usdc.balanceOf(address(c)), TRANCHE);
        assertEq(c.credit(owner), TRANCHE);
    }

    function test_revert_sweepNotOwner() public {
        _resolveAll();
        vm.prank(beneficiary);
        vm.expectRevert(PTCommitment.NotOwner.selector);
        c.sweep();
    }

    function test_revert_sweepUnfunded() public {
        PTCommitment fresh = _deploy();
        vm.prank(owner);
        vm.expectRevert(PTCommitment.NotFunded.selector);
        fresh.sweep();
    }

    function test_revert_sweepWithUnresolvedDays() public {
        usdc.mint(address(c), 777);
        _seed(0);
        _claim(0);
        vm.prank(owner);
        vm.expectRevert(PTCommitment.DaysUnresolved.selector);
        c.sweep();
    }

    function test_revert_sweepNothing() public {
        _resolveAll();
        vm.prank(owner);
        vm.expectRevert(PTCommitment.NothingToSweep.selector);
        c.sweep();
    }
}

contract VerifierRotationTest is Base {
    uint256 internal newPk = 0xB0B;
    address internal newVerifier;

    function setUp() public override {
        super.setUp();
        newVerifier = vm.addr(newPk);
        _fund();
    }

    function test_rotate() public {
        vm.expectEmit(address(c));
        emit PTCommitment.VerifierProposed(newVerifier, block.timestamp + 48 hours);
        vm.prank(owner);
        c.proposeVerifier(newVerifier);
        assertEq(c.pendingVerifier(), newVerifier);

        vm.warp(block.timestamp + 48 hours - 1);
        uint256 readyAt = c.pendingVerifierReadyAt();
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.VerifierTimelocked.selector, readyAt));
        c.acceptVerifier();

        vm.warp(block.timestamp + 1);
        vm.expectEmit(address(c));
        emit PTCommitment.VerifierRotated(verifierAddr, newVerifier);
        vm.prank(owner);
        c.acceptVerifier();
        assertEq(c.verifier(), newVerifier);
        assertEq(c.pendingVerifier(), address(0));
        assertEq(c.pendingVerifierReadyAt(), 0);
    }

    function test_oldKeyValidDuringTimelockAndInvalidAfter() public {
        vm.prank(owner);
        c.proposeVerifier(newVerifier);
        _seed(0);
        _claim(0); // old key still works
        _warpTo(c.dayStart(0) + 48 hours);
        vm.prank(owner);
        c.acceptVerifier();
        _seed(2);
        bytes memory oldSig = _sign(c, verifierPk, 2, VIDEO, SCORE);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(2, VIDEO, SCORE, oldSig);
        c.claim(2, VIDEO, SCORE, _sign(c, newPk, 2, VIDEO, SCORE));
    }

    function test_reproposeRestartsClock() public {
        vm.prank(owner);
        c.proposeVerifier(newVerifier);
        vm.warp(block.timestamp + 47 hours);
        vm.prank(owner);
        c.proposeVerifier(vm.addr(0xC0C));
        vm.warp(block.timestamp + 2 hours);
        uint256 readyAt = c.pendingVerifierReadyAt();
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.VerifierTimelocked.selector, readyAt));
        c.acceptVerifier();
    }

    function test_rotateWorksBeforeFunding() public {
        PTCommitment fresh = _deploy();
        vm.prank(owner);
        fresh.proposeVerifier(newVerifier);
        vm.warp(block.timestamp + 48 hours);
        vm.prank(owner);
        fresh.acceptVerifier();
        assertEq(fresh.verifier(), newVerifier);
    }

    function test_revert_proposeNotOwner() public {
        vm.prank(verifierAddr);
        vm.expectRevert(PTCommitment.NotOwner.selector);
        c.proposeVerifier(newVerifier);
    }

    function test_revert_proposeZero() public {
        vm.prank(owner);
        vm.expectRevert(PTCommitment.ZeroAddress.selector);
        c.proposeVerifier(address(0));
    }

    function test_revert_proposeSame() public {
        vm.prank(owner);
        vm.expectRevert(PTCommitment.SameVerifier.selector);
        c.proposeVerifier(verifierAddr);
    }

    function test_revert_acceptNotOwner() public {
        vm.prank(owner);
        c.proposeVerifier(newVerifier);
        vm.warp(block.timestamp + 48 hours);
        vm.prank(newVerifier);
        vm.expectRevert(PTCommitment.NotOwner.selector);
        c.acceptVerifier();
    }

    function test_revert_acceptNoPending() public {
        vm.prank(owner);
        vm.expectRevert(PTCommitment.NoPendingVerifier.selector);
        c.acceptVerifier();
    }
}

contract MiscTest is Base {
    function test_views() public view {
        assertEq(c.dayStart(0), start);
        assertEq(c.dayEnd(0), start + 1 days);
        assertEq(c.claimDeadline(0), start + 1 days + GRACE);
        assertEq(c.dayStart(5), start + 5 days);
        assertTrue(c.isActive(0));
        assertFalse(c.isActive(REST_DAY));
        assertFalse(c.isActive(NUM_DAYS));
        assertFalse(c.isActive(type(uint256).max));
    }

    function test_rejectsEth() public {
        vm.deal(stranger, 1 ether);
        vm.prank(stranger);
        (bool ok,) = address(c).call{value: 1 ether}("");
        assertFalse(ok);
        vm.prank(stranger);
        (ok,) = address(c).call{value: 1 ether}(abi.encodeWithSignature("nonexistent()"));
        assertFalse(ok);
        assertEq(address(c).balance, 0);
    }

    function test_fullLifecycle() public {
        _fund();
        uint256[] memory list = _activeDays();
        // Claim every other active day, forfeit the rest.
        for (uint256 i; i < list.length; ++i) {
            if (i % 2 == 0) {
                _seed(list[i]);
                _claim(list[i]);
            }
        }
        _warpTo(c.claimDeadline(NUM_DAYS - 1) + 1);
        c.forfeitMany(list);
        assertEq(c.unresolvedDays(), 0);
        vm.prank(owner);
        c.withdraw();
        vm.prank(beneficiary);
        c.withdraw();
        assertEq(usdc.balanceOf(owner), TRANCHE * 6);
        assertEq(usdc.balanceOf(beneficiary), TRANCHE * 6);
        assertEq(usdc.balanceOf(address(c)), 0);
        for (uint256 i; i < usdc.recipientCount(); ++i) {
            address r = usdc.recipientsOfWatched(i);
            assertTrue(r == owner || r == beneficiary);
        }
    }
}

contract ReentrancyTest is Base {
    function test_reentrantTokenCannotReenterWithdraw() public {
        ReentrantToken bad = new ReentrantToken();
        PTCommitment p = new PTCommitment(
            owner, beneficiary, verifierAddr, IERC20(address(bad)), start, NUM_DAYS, BITMAP, TRANCHE, GRACE
        );
        bad.mint(owner, p.totalRequired());
        vm.startPrank(owner);
        bad.approve(address(p), p.totalRequired());
        p.fund();
        vm.stopPrank();

        _warpTo(p.claimDeadline(0) + 1);
        p.forfeit(0);
        bad.arm(address(p), abi.encodeCall(PTCommitment.withdraw, ()));
        vm.prank(beneficiary);
        vm.expectRevert(ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        p.withdraw();
        assertEq(p.credit(beneficiary), TRANCHE);
    }
}

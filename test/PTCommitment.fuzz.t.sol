// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PTCommitment} from "../src/PTCommitment.sol";
import {Base} from "./Base.t.sol";

contract FuzzTest is Base {
    function _popcount(uint256 x) internal pure returns (uint256 n) {
        for (uint256 i; i < 256; ++i) {
            if ((x >> i) & 1 == 1) ++n;
        }
    }

    function testFuzz_constructorSchedule(uint256 numDays, uint256 bitmap) public {
        numDays = bound(numDays, 0, 300);
        bool valid = numDays >= 1 && numDays <= 256 && bitmap != 0 && (numDays == 256 || bitmap >> numDays == 0);
        if (!valid) {
            if (numDays == 0 || numDays > 256) vm.expectRevert(PTCommitment.InvalidNumDays.selector);
            else vm.expectRevert(PTCommitment.InvalidSchedule.selector);
        }
        PTCommitment p =
            new PTCommitment(owner, beneficiary, verifierAddr, IERC20(address(usdc)), start, numDays, bitmap, 1, GRACE);
        if (valid) {
            assertEq(p.activeDayCount(), _popcount(bitmap));
            assertEq(p.totalRequired(), _popcount(bitmap));
            assertEq(p.unresolvedDays(), _popcount(bitmap));
        }
    }

    function testFuzz_validBitmapWithinNumDays(uint256 numDays, uint256 bitmap) public {
        numDays = bound(numDays, 1, 256);
        if (numDays < 256) bitmap &= (uint256(1) << numDays) - 1;
        vm.assume(bitmap != 0);
        PTCommitment p =
            new PTCommitment(owner, beneficiary, verifierAddr, IERC20(address(usdc)), start, numDays, bitmap, 3, GRACE);
        assertEq(p.activeDayCount(), _popcount(bitmap));
        assertEq(p.totalRequired(), 3 * _popcount(bitmap));
    }

    function testFuzz_isActive(uint256 d) public view {
        bool expected = d < NUM_DAYS && (BITMAP >> d) & 1 == 1;
        assertEq(c.isActive(d), expected);
    }

    function testFuzz_inactiveDaysRejected(uint256 d) public {
        vm.assume(!(d < NUM_DAYS && (BITMAP >> d) & 1 == 1));
        _fund();
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, d));
        c.seedDay(d);
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, d));
        c.claim(d, VIDEO, SCORE, "");
        vm.expectRevert(abi.encodeWithSelector(PTCommitment.InactiveDay.selector, d));
        c.forfeit(d);
    }

    /// Picks either a window edge (±1s) or a random offset, relative to dayStart.
    function _offset(uint8 selector, uint256 random) internal pure returns (int256) {
        int256 dayI = int256(DAY_SECONDS);
        int256 graceI = int256(GRACE);
        int256[9] memory edges =
            [int256(-1), 0, 1, dayI - 1, dayI, dayI + graceI - 1, dayI + graceI, dayI + graceI + 1, dayI + graceI + 2];
        if (selector % 2 == 0) return edges[selector / 2 % 9];
        return int256(bound(random, 0, uint256(dayI + graceI) + 2 days)) - 1 days;
    }

    uint256 internal constant DAY_SECONDS = 86400;

    function _activeDay(uint256 seed) internal pure returns (uint256) {
        uint256[] memory list = _activeDays();
        return list[seed % list.length];
    }

    function testFuzz_seedWindow(uint256 daySeed, uint8 selector, uint256 random) public {
        _fund();
        uint256 d = _activeDay(daySeed);
        uint256 t = uint256(int256(c.dayStart(d)) + _offset(selector, random));
        _warpTo(t);
        if (t < c.dayStart(d)) {
            vm.expectRevert(abi.encodeWithSelector(PTCommitment.DayNotStarted.selector, d));
        } else if (t > c.claimDeadline(d)) {
            vm.expectRevert(abi.encodeWithSelector(PTCommitment.ClaimWindowClosed.selector, d));
        }
        c.seedDay(d);
    }

    /// Claim and forfeit windows are disjoint and together cover all time after dayStart.
    function testFuzz_claimXorForfeit(uint256 daySeed, uint8 selector, uint256 random) public {
        _fund();
        uint256 d = _activeDay(daySeed);
        _seed(d);
        bytes memory sig = _sign(d);
        int256 off = _offset(selector, random);
        if (off < 0) off = -off;
        uint256 t = c.dayStart(d) + uint256(off);
        _warpTo(t);

        uint256 snap = vm.snapshotState();
        bool claimOk;
        try c.claim(d, VIDEO, SCORE, sig) {
            claimOk = true;
        } catch {}
        vm.revertToState(snap);
        bool forfeitOk;
        try c.forfeit(d) {
            forfeitOk = true;
        } catch {}

        assertTrue(claimOk != forfeitOk, "exactly one of claim/forfeit must succeed");
        assertEq(claimOk, t <= c.claimDeadline(d));
    }

    function testFuzz_lifecycle(uint256 tranche, uint256 numDays, uint256 bitmap, uint256 claimMask) public {
        tranche = bound(tranche, 1, 1e15); // up to 1e9 USDC
        numDays = bound(numDays, 1, 40);
        bitmap &= (uint256(1) << numDays) - 1;
        vm.assume(bitmap != 0);
        PTCommitment p = new PTCommitment(
            owner, beneficiary, verifierAddr, IERC20(address(usdc)), start, numDays, bitmap, tranche, GRACE
        );
        usdc.watch(address(p));
        _fund(p);

        uint256 claimed;
        uint256[] memory active = new uint256[](p.activeDayCount());
        uint256 j;
        for (uint256 d; d < numDays; ++d) {
            if ((bitmap >> d) & 1 == 0) continue;
            active[j++] = d;
            if ((claimMask >> d) & 1 == 1) {
                _warpTo(p.dayStart(d));
                p.seedDay(d);
                p.claim(d, VIDEO, SCORE, _sign(p, verifierPk, d, VIDEO, SCORE));
                ++claimed;
            }
        }
        _warpTo(p.claimDeadline(numDays - 1) + 1);
        p.forfeitMany(active);
        assertEq(p.unresolvedDays(), 0);

        if (claimed > 0) {
            vm.prank(owner);
            p.withdraw();
        }
        if (claimed < active.length) {
            vm.prank(beneficiary);
            p.withdraw();
        }
        assertEq(usdc.balanceOf(owner), claimed * tranche);
        assertEq(usdc.balanceOf(beneficiary), (active.length - claimed) * tranche);
        assertEq(usdc.balanceOf(address(p)), 0);
        for (uint256 i; i < usdc.recipientCount(); ++i) {
            address r = usdc.recipientsOfWatched(i);
            assertTrue(r == owner || r == beneficiary);
        }
    }

    function testFuzz_wrongKeyRejected(uint256 pk) public {
        pk = bound(pk, 1, 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364140);
        vm.assume(pk != verifierPk);
        _fund();
        _seed(0);
        bytes memory sig = _sign(c, pk, 0, VIDEO, SCORE);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sig);
    }

    function testFuzz_garbageSignatureRejected(bytes calldata sig) public {
        _fund();
        _seed(0);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, VIDEO, SCORE, sig);
    }

    function testFuzz_signedFieldsBound(bytes32 vh, uint32 score, bytes32 vh2, uint32 score2) public {
        vm.assume(vh != vh2 || score != score2);
        _fund();
        _seed(0);
        bytes memory sig = _sign(c, verifierPk, 0, vh, score);
        vm.expectRevert(PTCommitment.InvalidSignature.selector);
        c.claim(0, vh2, score2, sig);
        c.claim(0, vh, score, sig);
    }
}

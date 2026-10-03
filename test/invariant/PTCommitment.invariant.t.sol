// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {StdInvariant} from "forge-std/StdInvariant.sol";
import {PTCommitment} from "../../src/PTCommitment.sol";
import {Base} from "../Base.t.sol";
import {Handler} from "./Handler.sol";

contract PTCommitmentInvariantTest is StdInvariant, Base {
    Handler internal handler;

    function setUp() public override {
        super.setUp();
        _fund();
        vm.warp(start - 1 hours);
        handler = new Handler(c, usdc, verifierPk);
        targetContract(address(handler));
    }

    function _bal() internal view returns (uint256) {
        return usdc.balanceOf(address(c));
    }

    /// SPEC §7.1
    function invariant_balanceCoversCredits() public view {
        assertGe(_bal(), c.totalCredited());
    }

    /// SPEC §7.2
    function invariant_balanceCoversCreditsAndUnresolved() public view {
        assertGe(_bal(), c.totalCredited() + c.unresolvedDays() * TRANCHE);
    }

    /// SPEC §7.3
    function invariant_dayCountsConserved() public view {
        assertEq(handler.ghostClaimed() + handler.ghostForfeited() + c.unresolvedDays(), ACTIVE);
    }

    /// SPEC §7.4
    function invariant_creditsConserved() public view {
        assertEq(
            (handler.ghostClaimed() + handler.ghostForfeited()) * TRANCHE, c.totalCredited() + handler.ghostWithdrawn()
        );
        assertEq(c.totalCredited(), c.credit(owner) + c.credit(beneficiary));
    }

    /// Exact token accounting: everything in = everything out + what's still held.
    function invariant_tokenConservation() public view {
        assertEq(TRANCHE * ACTIVE + handler.ghostDonated(), _bal() + handler.ghostWithdrawn() + handler.ghostSwept());
    }

    /// SPEC §7.5 and §7.6
    function invariant_daysResolveOnceAndRestDaysUntouched() public view {
        for (uint256 d; d < NUM_DAYS + 2; ++d) {
            uint256 n = handler.ghostResolutions(d);
            PTCommitment.DayState s = c.dayState(d);
            assertLe(n, 1);
            if (!c.isActive(d)) {
                assertEq(uint8(s), uint8(PTCommitment.DayState.Unseeded));
                assertEq(c.challenge(d), bytes32(0));
            } else {
                bool resolved = s == PTCommitment.DayState.Claimed || s == PTCommitment.DayState.Forfeited;
                assertEq(resolved, n == 1);
            }
        }
    }

    /// SPEC §7.7
    function invariant_onlyPayeesHoldCredit() public view {
        assertEq(c.credit(address(handler)), 0);
        assertEq(c.credit(verifierAddr), 0);
        assertEq(c.credit(address(c)), 0);
        assertEq(c.credit(relayer), 0);
        assertFalse(handler.ghostStrangerWithdrew());
    }

    /// SPEC §7.8
    function invariant_tokensOnlyLeaveToPayees() public view {
        for (uint256 i; i < usdc.recipientCount(); ++i) {
            address r = usdc.recipientsOfWatched(i);
            assertTrue(r == owner || r == beneficiary);
        }
    }

    /// SPEC §7.9
    function invariant_challengeImmutable() public view {
        assertFalse(handler.ghostChallengeChanged());
    }

    /// SPEC §7.11
    function invariant_verifierOnlyRotatesAfterTimelock() public view {
        assertFalse(handler.ghostVerifierRotatedEarly());
        assertEq(c.verifier(), handler.ghostVerifier());
    }

    function afterInvariant() external view {
        // Coverage sanity: the run should reach every action.
        assertGt(handler.calls("claim"), 0);
        assertGt(handler.calls("forfeit"), 0);
    }
}

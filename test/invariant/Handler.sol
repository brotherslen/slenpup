// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {PTCommitment} from "../../src/PTCommitment.sol";
import {MockUSDC} from "../mocks/MockUSDC.sol";

/// @dev Drives the commitment through random sequences of every external action, with ghost
///      accounting the invariants compare against.
contract Handler is Test {
    PTCommitment public immutable c;
    MockUSDC public immutable usdc;
    address public immutable owner;
    address public immutable beneficiary;
    uint256 internal verifierPk;
    uint256 internal pendingPk;

    uint256 public ghostClaimed;
    uint256 public ghostForfeited;
    uint256 public ghostWithdrawn;
    uint256 public ghostSwept;
    uint256 public ghostDonated;
    mapping(uint256 => uint256) public ghostResolutions;
    mapping(uint256 => bytes32) public ghostChallenge;
    address public ghostVerifier;
    uint256 public ghostProposedAt;
    bool public ghostVerifierRotatedEarly;
    bool public ghostChallengeChanged;
    bool public ghostStrangerWithdrew;

    mapping(bytes32 => uint256) public calls;

    constructor(PTCommitment c_, MockUSDC usdc_, uint256 verifierPk_) {
        c = c_;
        usdc = usdc_;
        owner = c_.owner();
        beneficiary = c_.beneficiary();
        verifierPk = verifierPk_;
        ghostVerifier = c_.verifier();
    }

    function _day(uint256 seed) internal view returns (uint256) {
        // Mostly in-range days, sometimes out of range or rest days.
        return bound(seed, 0, c.numDays() + 1);
    }

    function _syncChallenge(uint256 d) internal {
        bytes32 ch = c.challenge(d);
        if (ghostChallenge[d] != bytes32(0) && ghostChallenge[d] != ch) ghostChallengeChanged = true;
        if (ch != bytes32(0)) ghostChallenge[d] = ch;
    }

    function warp(uint256 secs) external {
        calls["warp"]++;
        secs = bound(secs, 1, 30 hours);
        vm.warp(block.timestamp + secs);
        vm.roll(block.number + 1);
    }

    function seed(uint256 daySeed) external {
        calls["seed"]++;
        uint256 d = _day(daySeed);
        try c.seedDay(d) {
            if (ghostChallenge[d] != bytes32(0)) ghostChallengeChanged = true;
            if (c.dayStart(d) > block.timestamp) ghostChallengeChanged = true; // seeded before day start
        } catch {}
        _syncChallenge(d);
    }

    function claim(uint256 daySeed, bytes32 videoHash, uint32 score, bool useWrongKey) external {
        calls["claim"]++;
        uint256 d = _day(daySeed);
        uint256 pk = useWrongKey ? 0xDEAD : verifierPk;
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, c.claimDigest(d, videoHash, score));
        try c.claim(d, videoHash, score, abi.encodePacked(r, s, v)) {
            ghostClaimed++;
            ghostResolutions[d]++;
        } catch {}
        _syncChallenge(d);
    }

    function forfeit(uint256 daySeed) external {
        calls["forfeit"]++;
        uint256 d = _day(daySeed);
        try c.forfeit(d) {
            ghostForfeited++;
            ghostResolutions[d]++;
        } catch {}
    }

    function forfeitMany(uint256 a, uint256 b, uint256 cc) external {
        calls["forfeitMany"]++;
        uint256[] memory list = new uint256[](3);
        list[0] = _day(a);
        list[1] = _day(b);
        list[2] = _day(cc);
        uint256[3] memory before;
        for (uint256 i; i < 3; ++i) {
            before[i] = uint8(c.dayState(list[i]));
        }
        try c.forfeitMany(list) {
            // count each distinct day that moved to Forfeited
            for (uint256 i; i < 3; ++i) {
                bool dup;
                for (uint256 k; k < i; ++k) {
                    if (list[k] == list[i]) dup = true;
                }
                if (!dup && before[i] < uint8(PTCommitment.DayState.Claimed)) {
                    ghostForfeited++;
                    ghostResolutions[list[i]]++;
                }
            }
        } catch {}
    }

    function withdraw(bool asOwner) external {
        calls["withdraw"]++;
        address who = asOwner ? owner : beneficiary;
        uint256 amount = c.credit(who);
        vm.prank(who);
        try c.withdraw() {
            ghostWithdrawn += amount;
        } catch {}
    }

    function withdrawStranger(address who) external {
        calls["withdrawStranger"]++;
        vm.prank(who);
        try c.withdraw() {
            if (who != owner && who != beneficiary) ghostStrangerWithdrew = true;
        } catch {}
    }

    function donate(uint256 amount) external {
        calls["donate"]++;
        amount = bound(amount, 1, 1e12);
        usdc.mint(address(c), amount);
        ghostDonated += amount;
    }

    function sweep(bool asOwner) external {
        calls["sweep"]++;
        uint256 balBefore = usdc.balanceOf(address(c));
        vm.prank(asOwner ? owner : beneficiary);
        try c.sweep() {
            ghostSwept += balBefore - usdc.balanceOf(address(c));
        } catch {}
    }

    function proposeVerifier(uint256 pkSeed) external {
        calls["propose"]++;
        uint256 pk = bound(pkSeed, 1, 1e30);
        vm.prank(owner);
        try c.proposeVerifier(vm.addr(pk)) {
            pendingPk = pk;
            ghostProposedAt = block.timestamp;
        } catch {}
    }

    function acceptVerifier() external {
        calls["accept"]++;
        vm.prank(owner);
        try c.acceptVerifier() {
            if (block.timestamp < ghostProposedAt + 48 hours) ghostVerifierRotatedEarly = true;
            verifierPk = pendingPk;
            ghostVerifier = c.verifier();
        } catch {}
    }
}

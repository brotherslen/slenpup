// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PTCommitment} from "../src/PTCommitment.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

abstract contract Base is Test {
    MockUSDC internal usdc;
    PTCommitment internal c;

    address internal owner = makeAddr("owner");
    address internal beneficiary = makeAddr("beneficiary");
    address internal relayer = makeAddr("relayer");
    address internal stranger = makeAddr("stranger");
    uint256 internal verifierPk = 0xA11CE;
    address internal verifierAddr;

    uint256 internal constant T0 = 1_790_000_000;
    uint256 internal constant NUM_DAYS = 14;
    // Days 3 and 10 are rest days: 0x3FFF minus bits 3 and 10. 12 active days.
    uint256 internal constant BITMAP = 0x3BF7;
    uint256 internal constant ACTIVE = 12;
    uint256 internal constant TRANCHE = 10e6;
    uint256 internal constant GRACE = 6 hours;
    uint256 internal constant REST_DAY = 3;

    uint256 internal start;

    bytes32 internal constant VIDEO = keccak256("video");
    uint32 internal constant SCORE = 97;

    function setUp() public virtual {
        vm.warp(T0);
        vm.roll(1_000);
        verifierAddr = vm.addr(verifierPk);
        usdc = new MockUSDC();
        start = (T0 / 1 hours + 25) * 1 hours;
        c = _deploy();
        usdc.watch(address(c));
    }

    function _deploy() internal returns (PTCommitment) {
        return new PTCommitment(
            owner, beneficiary, verifierAddr, IERC20(address(usdc)), start, NUM_DAYS, BITMAP, TRANCHE, GRACE
        );
    }

    function _fund() internal {
        _fund(c);
    }

    function _fund(PTCommitment target) internal {
        uint256 amount = target.totalRequired();
        usdc.mint(owner, amount);
        vm.startPrank(owner);
        usdc.approve(address(target), amount);
        target.fund();
        vm.stopPrank();
    }

    function _sign(PTCommitment target, uint256 pk, uint256 d, bytes32 vh, uint32 score)
        internal
        view
        returns (bytes memory)
    {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, target.claimDigest(d, vh, score));
        return abi.encodePacked(r, s, v);
    }

    function _sign(uint256 d) internal view returns (bytes memory) {
        return _sign(c, verifierPk, d, VIDEO, SCORE);
    }

    /// @dev Moves time forward only; also advances the block so blockhash(block.number - 1) changes.
    function _warpTo(uint256 t) internal {
        require(t >= block.timestamp, "test warps backwards");
        vm.warp(t);
        vm.roll(block.number + 1);
    }

    function _seedAt(uint256 d, uint256 t) internal {
        _warpTo(t);
        c.seedDay(d);
    }

    function _seed(uint256 d) internal {
        _seedAt(d, c.dayStart(d));
    }

    function _claim(uint256 d) internal {
        bytes memory sig = _sign(d);
        vm.prank(relayer);
        c.claim(d, VIDEO, SCORE, sig);
    }

    /// @dev EIP-712 digest computed independently of the contract, straight from SPEC.md §6.1.
    function _specDigest(PTCommitment target, uint256 d, bytes32 ch, bytes32 vh, uint32 score)
        internal
        view
        returns (bytes32)
    {
        bytes32 domain = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256("PTCommitment"),
                keccak256("1"),
                block.chainid,
                address(target)
            )
        );
        bytes32 structHash = keccak256(
            abi.encode(keccak256("Claim(uint256 day,bytes32 challenge,bytes32 videoHash,uint32 score)"), d, ch, vh, score)
        );
        return keccak256(abi.encodePacked("\x19\x01", domain, structHash));
    }

    function _activeDays() internal pure returns (uint256[] memory list) {
        list = new uint256[](ACTIVE);
        uint256 j;
        for (uint256 d; d < NUM_DAYS; ++d) {
            if ((BITMAP >> d) & 1 == 1) list[j++] = d;
        }
    }
}

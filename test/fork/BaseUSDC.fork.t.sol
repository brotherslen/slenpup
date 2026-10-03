// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {PTCommitment} from "../../src/PTCommitment.sol";

/// @dev Circle FiatToken admin surface used to blacklist an address on the fork.
interface IFiatToken {
    function blacklister() external view returns (address);
    function blacklist(address account) external;
    function isBlacklisted(address account) external view returns (bool);
}

/// Runs against real USDC on a Base mainnet fork. Skipped unless BASE_RPC_URL is set.
///
/// USDC on Base: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
/// Source: https://developers.circle.com/stablecoins/usdc-contract-addresses
/// STATUS: NOT YET VERIFIED against that page (it was unreachable from the build sandbox).
///         Check it by hand before relying on this test; test_tokenIdentity checks name/symbol/decimals on chain.
contract BaseUSDCForkTest is Test {
    address internal constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    uint256 internal constant BASE_CHAIN_ID = 8453;

    PTCommitment internal c;
    address internal owner = makeAddr("owner");
    address internal beneficiary = makeAddr("beneficiary");
    uint256 internal verifierPk = 0xA11CE;
    uint256 internal start;
    uint256 internal constant TRANCHE = 5e6;

    function setUp() public {
        string memory rpc = vm.envOr("BASE_RPC_URL", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpc);
        assertEq(block.chainid, BASE_CHAIN_ID);
        start = (block.timestamp / 1 hours + 2) * 1 hours;
        // 3 days, all active.
        c = new PTCommitment(owner, beneficiary, vm.addr(verifierPk), IERC20(USDC), start, 3, 0x7, TRANCHE, 6 hours);
        deal(USDC, owner, c.totalRequired());
        vm.startPrank(owner);
        IERC20(USDC).approve(address(c), c.totalRequired());
        c.fund();
        vm.stopPrank();
    }

    function test_tokenIdentity() public view {
        assertEq(IERC20Metadata(USDC).symbol(), "USDC");
        assertEq(IERC20Metadata(USDC).name(), "USD Coin");
        assertEq(IERC20Metadata(USDC).decimals(), 6);
        assertEq(IERC20(USDC).balanceOf(address(c)), 3 * TRANCHE);
    }

    function _claim(uint256 d) internal {
        vm.warp(c.dayStart(d));
        vm.roll(block.number + 1);
        c.seedDay(d);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(verifierPk, c.claimDigest(d, keccak256("v"), 90));
        c.claim(d, keccak256("v"), 90, abi.encodePacked(r, s, v));
    }

    function test_lifecycleWithRealUSDC() public {
        _claim(0);
        _claim(1);
        vm.warp(c.claimDeadline(2) + 1);
        c.forfeit(2);
        vm.prank(owner);
        c.withdraw();
        vm.prank(beneficiary);
        c.withdraw();
        assertEq(IERC20(USDC).balanceOf(owner), 2 * TRANCHE);
        assertEq(IERC20(USDC).balanceOf(beneficiary), TRANCHE);
        assertEq(IERC20(USDC).balanceOf(address(c)), 0);
    }

    /// Real Circle blacklist: a blacklisted beneficiary can't withdraw, and that doesn't block the owner.
    function test_realBlacklistIsolation() public {
        IFiatToken fiat = IFiatToken(USDC);
        vm.prank(fiat.blacklister());
        fiat.blacklist(beneficiary);
        assertTrue(fiat.isBlacklisted(beneficiary));

        _claim(0);
        vm.warp(c.claimDeadline(1) + 1);
        c.forfeit(1);

        vm.prank(beneficiary);
        vm.expectRevert();
        c.withdraw();
        assertEq(c.credit(beneficiary), TRANCHE);

        vm.prank(owner);
        c.withdraw();
        assertEq(IERC20(USDC).balanceOf(owner), TRANCHE);
    }
}

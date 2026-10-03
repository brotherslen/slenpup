// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {Script, console2} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PTCommitment} from "../src/PTCommitment.sol";

/// Deploys one PTCommitment. Two-step by design:
///   1. Dry run (no --broadcast): prints every parameter and a PARAMS_HASH.
///   2. Check every line, then rerun with --broadcast and CONFIRM_PARAMS_HASH=<that hash>.
/// Any parameter change produces a different hash, so a broadcast can't use parameters you didn't see.
///
/// Env: OWNER BENEFICIARY VERIFIER TOKEN START_TIME NUM_DAYS SCHEDULE_BITMAP TRANCHE_AMOUNT GRACE_SECONDS
/// Use script/start_time.py for START_TIME and SCHEDULE_BITMAP. Sign with --ledger or --account <keystore>.
contract Deploy is Script {
    // USDC addresses. Recalled, NOT yet verified against https://developers.circle.com/stablecoins/usdc-contract-addresses.
    // Verify both before the first broadcast (see DEPLOY_CHECKLIST.md).
    address internal constant USDC_BASE = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address internal constant USDC_BASE_SEPOLIA = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;

    struct Params {
        address owner;
        address beneficiary;
        address verifier;
        address token;
        uint256 startTime;
        uint256 numDays;
        uint256 scheduleBitmap;
        uint256 trancheAmount;
        uint256 graceSeconds;
    }

    function _load() internal view returns (Params memory p) {
        p.owner = vm.envAddress("OWNER");
        p.beneficiary = vm.envAddress("BENEFICIARY");
        p.verifier = vm.envAddress("VERIFIER");
        p.token = vm.envAddress("TOKEN");
        p.startTime = vm.envUint("START_TIME");
        p.numDays = vm.envUint("NUM_DAYS");
        p.scheduleBitmap = vm.envUint("SCHEDULE_BITMAP");
        p.trancheAmount = vm.envUint("TRANCHE_AMOUNT");
        p.graceSeconds = vm.envUint("GRACE_SECONDS");
    }

    function _popcount(uint256 x) internal pure returns (uint256 n) {
        while (x != 0) {
            x &= x - 1;
            ++n;
        }
    }

    function _usdc(uint256 amount) internal pure returns (string memory) {
        uint256 cents = amount % 1e6;
        string memory frac = vm.toString(cents + 1e6); // leading 1 keeps zero padding
        bytes memory f = bytes(frac);
        bytes memory six = new bytes(6);
        for (uint256 i; i < 6; ++i) {
            six[i] = f[i + 1];
        }
        return string.concat(vm.toString(amount / 1e6), ".", string(six), " USDC");
    }

    function _checkToken(address token) internal view {
        if (block.chainid == 8453) require(token == USDC_BASE, "TOKEN is not Base USDC");
        else if (block.chainid == 84532) require(token == USDC_BASE_SEPOLIA, "TOKEN is not Base Sepolia USDC");
        else require(block.chainid == 31337, "unknown chain");
    }

    function run() external returns (PTCommitment c) {
        Params memory p = _load();
        _checkToken(p.token);
        uint256 active = _popcount(p.scheduleBitmap);
        bytes32 paramsHash = keccak256(abi.encode(block.chainid, p));

        console2.log("==== PTCommitment deployment parameters ====");
        console2.log("chainId         ", block.chainid);
        console2.log("owner           ", p.owner);
        console2.log("beneficiary     ", p.beneficiary);
        console2.log("verifier        ", p.verifier);
        console2.log("token           ", p.token);
        console2.log("startTime (UTC) ", p.startTime);
        console2.log("  check with:    python3 script/start_time.py show", p.startTime);
        console2.log("numDays         ", p.numDays);
        console2.log("scheduleBitmap  ", vm.toString(bytes32(p.scheduleBitmap)));
        console2.log("active days     ", active);
        console2.log("trancheAmount   ", _usdc(p.trancheAmount));
        console2.log("totalRequired   ", _usdc(p.trancheAmount * active));
        console2.log("graceSeconds    ", p.graceSeconds);
        console2.log("PARAMS_HASH     ", vm.toString(paramsHash));

        bytes32 confirmed = vm.envOr("CONFIRM_PARAMS_HASH", bytes32(0));
        if (confirmed == bytes32(0)) {
            console2.log("Dry run only. Check every line above, then rerun with --broadcast and");
            console2.log("CONFIRM_PARAMS_HASH set to the PARAMS_HASH above.");
            return c;
        }
        require(confirmed == paramsHash, "CONFIRM_PARAMS_HASH does not match these parameters");

        vm.startBroadcast();
        c = new PTCommitment(
            p.owner,
            p.beneficiary,
            p.verifier,
            IERC20(p.token),
            p.startTime,
            p.numDays,
            p.scheduleBitmap,
            p.trancheAmount,
            p.graceSeconds
        );
        vm.stopBroadcast();
        console2.log("deployed        ", address(c));
        console2.log("Next: verify on Basescan, re-read every immutable there, THEN fund() from the owner wallet.");
    }
}

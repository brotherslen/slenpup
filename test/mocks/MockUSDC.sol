// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @dev 6-decimal ERC20 with a USDC-style blacklist, an optional transfer fee (to test the funding
///      balance-delta check), and a log of every address the watched contract sends tokens to.
contract MockUSDC is ERC20 {
    mapping(address => bool) public blacklisted;
    uint256 public feeBps;
    address public watched;
    address[] public recipientsOfWatched;

    error Blacklisted(address account);

    constructor() ERC20("Mock USDC", "mUSDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function setBlacklisted(address account, bool value) external {
        blacklisted[account] = value;
    }

    function setFeeBps(uint256 bps) external {
        feeBps = bps;
    }

    function watch(address target) external {
        watched = target;
    }

    function recipientCount() external view returns (uint256) {
        return recipientsOfWatched.length;
    }

    function _update(address from, address to, uint256 value) internal override {
        if (blacklisted[from]) revert Blacklisted(from);
        if (blacklisted[to]) revert Blacklisted(to);
        if (from != address(0) && to != address(0) && feeBps != 0) {
            uint256 fee = value * feeBps / 10_000;
            super._update(from, address(0), fee);
            value -= fee;
        }
        if (from == watched && from != address(0)) recipientsOfWatched.push(to);
        super._update(from, to, value);
    }
}

/// @dev Token whose transfer re-enters the commitment, to prove the reentrancy guard holds.
contract ReentrantToken is ERC20 {
    address public target;
    bytes public payload;

    constructor() ERC20("Reentrant", "RE") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function arm(address target_, bytes calldata payload_) external {
        target = target_;
        payload = payload_;
    }

    function _update(address from, address to, uint256 value) internal override {
        super._update(from, to, value);
        if (from == target && target != address(0)) {
            (bool ok, bytes memory ret) = target.call(payload);
            if (!ok) {
                assembly {
                    revert(add(ret, 32), mload(ret))
                }
            }
        }
    }
}

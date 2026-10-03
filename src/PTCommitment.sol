// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title PTCommitment
/// @notice Locks `totalRequired` of `token`. Each active day pays `trancheAmount` to `owner` if a
///         verifier-signed claim lands in that day's window, otherwise to `beneficiary`.
///         Every token transfer out goes to `owner` or `beneficiary`. See SPEC.md.
contract PTCommitment is EIP712, ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum DayState {
        Unseeded,
        Seeded,
        Claimed,
        Forfeited
    }

    uint256 public constant DAY = 86400;
    uint256 public constant MAX_DAYS = 256;
    uint256 public constant MAX_GRACE = 12 hours;
    uint256 public constant MAX_START_DELAY = 365 days;
    uint256 public constant VERIFIER_DELAY = 48 hours;
    bytes32 public constant CLAIM_TYPEHASH =
        keccak256("Claim(uint256 day,bytes32 challenge,bytes32 videoHash,uint32 score)");

    address public immutable owner;
    address public immutable beneficiary;
    IERC20 public immutable token;
    uint256 public immutable startTime;
    uint256 public immutable numDays;
    uint256 public immutable scheduleBitmap;
    uint256 public immutable trancheAmount;
    uint256 public immutable graceSeconds;
    uint256 public immutable activeDayCount;
    uint256 public immutable totalRequired;

    bool public funded;
    address public verifier;
    address public pendingVerifier;
    uint256 public pendingVerifierReadyAt;
    uint256 public unresolvedDays;
    uint256 public totalCredited;
    mapping(uint256 day => DayState) public dayState;
    mapping(uint256 day => bytes32) public challenge;
    mapping(address account => uint256) public credit;

    event Funded(address indexed payer, uint256 amount);
    event UnfundedReclaimed(uint256 amount);
    event DaySeeded(uint256 indexed day, bytes32 challenge);
    event DayClaimed(uint256 indexed day, bytes32 videoHash, uint32 score);
    event DayForfeited(uint256 indexed day);
    event Withdrawn(address indexed account, uint256 amount);
    event VerifierProposed(address indexed newVerifier, uint256 readyAt);
    event VerifierRotated(address indexed oldVerifier, address indexed newVerifier);
    event Swept(uint256 amount);

    error ZeroAddress();
    error InvalidPayee();
    error TokenHasNoCode();
    error InvalidStartTime();
    error InvalidNumDays();
    error InvalidSchedule();
    error ZeroTranche();
    error GraceTooLong();
    error NotOwner();
    error NotPayee();
    error AlreadyFunded();
    error NotFunded();
    error FundingClosed();
    error FundingAmountMismatch();
    error NotDead();
    error NothingToReclaim();
    error InactiveDay(uint256 day);
    error DayNotStarted(uint256 day);
    error ClaimWindowClosed(uint256 day);
    error ClaimWindowOpen(uint256 day);
    error AlreadySeeded(uint256 day);
    error NotSeeded(uint256 day);
    error AlreadyResolved(uint256 day);
    error InvalidSignature();
    error BatchTooLarge();
    error NothingToWithdraw();
    error SameVerifier();
    error NoPendingVerifier();
    error VerifierTimelocked(uint256 readyAt);
    error DaysUnresolved();
    error NothingToSweep();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(
        address owner_,
        address beneficiary_,
        address verifier_,
        IERC20 token_,
        uint256 startTime_,
        uint256 numDays_,
        uint256 scheduleBitmap_,
        uint256 trancheAmount_,
        uint256 graceSeconds_
    ) EIP712("PTCommitment", "1") {
        if (owner_ == address(0) || beneficiary_ == address(0) || verifier_ == address(0)) revert ZeroAddress();
        if (beneficiary_ == owner_ || owner_ == address(this) || beneficiary_ == address(this)) {
            revert InvalidPayee();
        }
        if (address(token_).code.length == 0) revert TokenHasNoCode();
        if (
            startTime_ <= block.timestamp || startTime_ > block.timestamp + MAX_START_DELAY
                || startTime_ % 1 hours != 0
        ) revert InvalidStartTime();
        if (numDays_ == 0 || numDays_ > MAX_DAYS) revert InvalidNumDays();
        if (scheduleBitmap_ == 0 || (numDays_ < MAX_DAYS && scheduleBitmap_ >> numDays_ != 0)) {
            revert InvalidSchedule();
        }
        if (trancheAmount_ == 0) revert ZeroTranche();
        if (graceSeconds_ > MAX_GRACE) revert GraceTooLong();

        uint256 count;
        uint256 bits = scheduleBitmap_;
        while (bits != 0) {
            bits &= bits - 1;
            ++count;
        }

        owner = owner_;
        beneficiary = beneficiary_;
        verifier = verifier_;
        token = token_;
        startTime = startTime_;
        numDays = numDays_;
        scheduleBitmap = scheduleBitmap_;
        trancheAmount = trancheAmount_;
        graceSeconds = graceSeconds_;
        activeDayCount = count;
        totalRequired = trancheAmount_ * count;
        unresolvedDays = count;
    }

    // ---------------------------------------------------------------- funding

    /// @notice Pulls exactly `totalRequired` from the caller. The caller gets no special rights.
    function fund() external nonReentrant {
        if (funded) revert AlreadyFunded();
        if (block.timestamp >= startTime) revert FundingClosed();
        funded = true;
        emit Funded(msg.sender, totalRequired);

        uint256 balanceBefore = token.balanceOf(address(this));
        token.safeTransferFrom(msg.sender, address(this), totalRequired);
        if (token.balanceOf(address(this)) - balanceBefore != totalRequired) revert FundingAmountMismatch();
    }

    /// @notice If the contract was never funded and `startTime` has passed, returns any balance to `owner`.
    function reclaimUnfunded() external nonReentrant {
        if (funded) revert AlreadyFunded();
        if (block.timestamp < startTime) revert NotDead();
        uint256 amount = token.balanceOf(address(this));
        if (amount == 0) revert NothingToReclaim();
        emit UnfundedReclaimed(amount);
        token.safeTransfer(owner, amount);
    }

    // ------------------------------------------------------------------- days

    /// @notice Stores the day's challenge. Unknowable before the day starts; can't be computed later
    ///         because `blockhash` only reaches back 256 blocks.
    function seedDay(uint256 d) external nonReentrant {
        _requireFundedActive(d);
        if (dayState[d] != DayState.Unseeded) revert AlreadySeeded(d);
        _requireClaimWindow(d);

        bytes32 c = keccak256(abi.encode(blockhash(block.number - 1), d, address(this)));
        challenge[d] = c;
        dayState[d] = DayState.Seeded;
        emit DaySeeded(d, c);
    }

    /// @notice Anyone may submit; the tranche is always credited to `owner`.
    function claim(uint256 d, bytes32 videoHash, uint32 score, bytes calldata sig) external nonReentrant {
        _requireFundedActive(d);
        DayState s = dayState[d];
        if (s == DayState.Unseeded) revert NotSeeded(d);
        if (s != DayState.Seeded) revert AlreadyResolved(d);
        _requireClaimWindow(d);

        (address signer, ECDSA.RecoverError err,) = ECDSA.tryRecover(claimDigest(d, videoHash, score), sig);
        if (err != ECDSA.RecoverError.NoError || signer != verifier) revert InvalidSignature();

        dayState[d] = DayState.Claimed;
        --unresolvedDays;
        credit[owner] += trancheAmount;
        totalCredited += trancheAmount;
        emit DayClaimed(d, videoHash, score);
    }

    function forfeit(uint256 d) external nonReentrant {
        _requireFundedActive(d);
        if (_isResolved(d)) revert AlreadyResolved(d);
        _forfeit(d);
    }

    /// @notice Batch forfeit. Already-resolved days are skipped so a front-run `forfeit` can't grief the batch.
    function forfeitMany(uint256[] calldata dayList) external nonReentrant {
        if (dayList.length > MAX_DAYS) revert BatchTooLarge();
        for (uint256 i; i < dayList.length; ++i) {
            uint256 d = dayList[i];
            _requireFundedActive(d);
            if (!_isResolved(d)) _forfeit(d);
        }
    }

    // ------------------------------------------------------------ withdrawals

    function withdraw() external nonReentrant {
        if (msg.sender != owner && msg.sender != beneficiary) revert NotPayee();
        uint256 amount = credit[msg.sender];
        if (amount == 0) revert NothingToWithdraw();
        credit[msg.sender] = 0;
        totalCredited -= amount;
        emit Withdrawn(msg.sender, amount);
        token.safeTransfer(msg.sender, amount);
    }

    /// @notice After every active day resolves, sends `owner` any balance above outstanding credits.
    function sweep() external nonReentrant onlyOwner {
        if (!funded) revert NotFunded();
        if (unresolvedDays != 0) revert DaysUnresolved();
        uint256 balance = token.balanceOf(address(this));
        if (balance <= totalCredited) revert NothingToSweep();
        uint256 amount = balance - totalCredited;
        emit Swept(amount);
        token.safeTransfer(owner, amount);
    }

    // ---------------------------------------------------------------- verifier

    function proposeVerifier(address newVerifier) external nonReentrant onlyOwner {
        if (newVerifier == address(0)) revert ZeroAddress();
        if (newVerifier == verifier) revert SameVerifier();
        uint256 readyAt = block.timestamp + VERIFIER_DELAY;
        pendingVerifier = newVerifier;
        pendingVerifierReadyAt = readyAt;
        emit VerifierProposed(newVerifier, readyAt);
    }

    function acceptVerifier() external nonReentrant onlyOwner {
        address next = pendingVerifier;
        if (next == address(0)) revert NoPendingVerifier();
        if (block.timestamp < pendingVerifierReadyAt) revert VerifierTimelocked(pendingVerifierReadyAt);
        address old = verifier;
        verifier = next;
        pendingVerifier = address(0);
        pendingVerifierReadyAt = 0;
        emit VerifierRotated(old, next);
    }

    // ------------------------------------------------------------------- views

    function isActive(uint256 d) public view returns (bool) {
        return d < numDays && (scheduleBitmap >> d) & 1 == 1;
    }

    function dayStart(uint256 d) public view returns (uint256) {
        return startTime + d * DAY;
    }

    /// @notice Exclusive end of day `d`.
    function dayEnd(uint256 d) public view returns (uint256) {
        return dayStart(d) + DAY;
    }

    /// @notice Inclusive last second a claim for day `d` can land.
    function claimDeadline(uint256 d) public view returns (uint256) {
        return dayEnd(d) + graceSeconds;
    }

    /// @notice EIP-712 digest the verifier signs. Uses the stored challenge, so it's only meaningful once seeded.
    function claimDigest(uint256 d, bytes32 videoHash, uint32 score) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(CLAIM_TYPEHASH, d, challenge[d], videoHash, score)));
    }

    // ---------------------------------------------------------------- internal

    function _requireFundedActive(uint256 d) private view {
        if (!funded) revert NotFunded();
        if (!isActive(d)) revert InactiveDay(d);
    }

    function _requireClaimWindow(uint256 d) private view {
        if (block.timestamp < dayStart(d)) revert DayNotStarted(d);
        if (block.timestamp > claimDeadline(d)) revert ClaimWindowClosed(d);
    }

    function _isResolved(uint256 d) private view returns (bool) {
        DayState s = dayState[d];
        return s == DayState.Claimed || s == DayState.Forfeited;
    }

    /// @dev Caller has checked funded, active, and unresolved.
    function _forfeit(uint256 d) private {
        if (block.timestamp <= claimDeadline(d)) revert ClaimWindowOpen(d);
        dayState[d] = DayState.Forfeited;
        --unresolvedDays;
        credit[beneficiary] += trancheAmount;
        totalCredited += trancheAmount;
        emit DayForfeited(d);
    }
}

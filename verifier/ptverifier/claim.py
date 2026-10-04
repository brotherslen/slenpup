"""Sign a passed day's claim with the verifier key and submit it from the relayer wallet.

videoHash for a day = sha256 over the passing clips' sha256 digests, concatenated in session order.
It commits to exactly which files counted without putting anything about the videos on chain.
"""

from __future__ import annotations

import hashlib

from eth_account import Account
from eth_account.messages import encode_typed_data

from .chain import SEEDED, Commitment

CLAIM_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Claim": [
        {"name": "day", "type": "uint256"},
        {"name": "challenge", "type": "bytes32"},
        {"name": "videoHash", "type": "bytes32"},
        {"name": "score", "type": "uint32"},
    ],
}

# Don't start a claim this close to the deadline; Base blocks are 2 s, this leaves room for a slow RPC.
DEADLINE_MARGIN_S = 120


def video_hash(report: dict) -> bytes:
    digests = [bytes.fromhex(e["clip"]["sha256"]) for e in report["exercises"].values() if e["passed"]]
    return hashlib.sha256(b"".join(digests)).digest()


def sign(c: Commitment, day: int, challenge: bytes, vhash: bytes, score: int, verifier_key: bytes) -> bytes:
    """EIP-712 signature, after checking our digest equals the contract's own claimDigest."""
    msg = {
        "types": CLAIM_TYPES,
        "primaryType": "Claim",
        "domain": {"name": "PTCommitment", "version": "1", "chainId": c.w3.eth.chain_id, "verifyingContract": c.c.address},
        "message": {"day": day, "challenge": challenge, "videoHash": vhash, "score": score},
    }
    signed = Account.sign_message(encode_typed_data(full_message=msg), private_key=verifier_key)
    on_chain = bytes(c.claim_digest(day, vhash, score))
    if bytes(signed.message_hash) != on_chain:
        raise RuntimeError("local EIP-712 digest differs from the contract's claimDigest; not submitting")
    return bytes(signed.signature)


def submit(c: Commitment, report: dict, verifier_key: bytes, relayer_key: bytes) -> str:
    """Signs and submits the claim for a passed report. Returns the tx hash."""
    if not report["passed"]:
        raise ValueError("report did not pass")
    day = report["day"]
    info = c.day(day)
    if info.state != SEEDED:
        raise RuntimeError(f"day {day} is not open for a claim (state {info.state})")
    if c.now() > info.claim_deadline - DEADLINE_MARGIN_S:
        raise RuntimeError(f"day {day} is too close to or past its claim deadline")
    challenge = c.finalized_challenge(day)
    if challenge == bytes(32):
        raise RuntimeError(f"day {day}'s challenge isn't finalized yet; retry shortly")
    if "0x" + challenge.hex() != report["challenge"]:
        raise RuntimeError("challenge changed since verification (reorg?); re-verify before claiming")
    vhash = video_hash(report)
    sig = sign(c, day, challenge, vhash, report["score"], verifier_key)
    return c.send(c.c.functions.claim(day, vhash, report["score"], sig), relayer_key)

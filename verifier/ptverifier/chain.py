"""Read the commitment contract and, for the relayer, call seedDay. Holds no verifier key."""

from __future__ import annotations

import time
from dataclasses import dataclass

from eth_account import Account
from web3 import Web3

ABI = [
    {"type": "function", "name": n, "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]}
    for n in ("startTime", "numDays", "scheduleBitmap", "graceSeconds")
] + [
    {"type": "function", "name": "funded", "stateMutability": "view", "inputs": [], "outputs": [{"type": "bool"}]},
    {"type": "function", "name": "dayState", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint8"}]},
    {"type": "function", "name": "challenge", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "bytes32"}]},
    {"type": "function", "name": "isActive", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "bool"}]},
    {"type": "function", "name": "dayStart", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "claimDeadline", "stateMutability": "view", "inputs": [{"type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"type": "function", "name": "seedDay", "stateMutability": "nonpayable", "inputs": [{"type": "uint256"}], "outputs": []},
]

UNSEEDED, SEEDED, CLAIMED, FORFEITED = range(4)
STATE_NAMES = ("unseeded", "seeded", "claimed", "forfeited")


@dataclass
class DayInfo:
    day: int
    active: bool
    state: int
    challenge: bytes
    day_start: int
    claim_deadline: int


class Commitment:
    def __init__(self, rpc_url: str, address: str):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.c = self.w3.eth.contract(address=Web3.to_checksum_address(address), abi=ABI)

    def now(self) -> int:
        return int(self.w3.eth.get_block("latest")["timestamp"])

    def schedule_bitmap(self) -> int:
        return int(self.c.functions.scheduleBitmap().call())

    def current_day(self, now: int | None = None) -> int | None:
        """Calendar day index right now, or None before startTime."""
        now = self.now() if now is None else now
        start = int(self.c.functions.startTime().call())
        return None if now < start else (now - start) // 86400

    def day(self, d: int) -> DayInfo:
        f = self.c.functions
        return DayInfo(
            day=d,
            active=bool(f.isActive(d).call()),
            state=int(f.dayState(d).call()),
            challenge=bytes(f.challenge(d).call()),
            day_start=int(f.dayStart(d).call()),
            claim_deadline=int(f.claimDeadline(d).call()),
        )

    def seed(self, d: int, private_key: bytes, timeout_s: int = 120) -> str:
        acct = Account.from_key(private_key)
        tx = self.c.functions.seedDay(d).build_transaction(
            {"from": acct.address, "nonce": self.w3.eth.get_transaction_count(acct.address), "chainId": self.w3.eth.chain_id}
        )
        signed = acct.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_s)
        if receipt["status"] != 1:
            raise RuntimeError(f"seedDay({d}) reverted: {tx_hash.hex()}")
        return tx_hash.hex()

    def wait_finalized(self, d: int, poll_s: float = 5.0, timeout_s: float = 1800, finalized: bool = True) -> bytes:
        """The day's challenge as of a finalized block, so a reorg can't change it after words go out
        (SPEC §8). On a chain without the 'finalized' tag (Anvil), the latest block is used."""
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                tag = "finalized" if finalized else "latest"
                ch = bytes(self.c.functions.challenge(d).call(block_identifier=tag))
            except Exception:
                ch = bytes(self.c.functions.challenge(d).call())
            if ch != bytes(32):
                return ch
            if time.monotonic() > deadline:
                raise TimeoutError(f"challenge for day {d} not finalized after {timeout_s}s")
            time.sleep(poll_s)


def load_keystore(path, password: str) -> bytes:
    import json
    from pathlib import Path

    return bytes(Account.decrypt(json.loads(Path(path).read_text(encoding="utf-8")), password))

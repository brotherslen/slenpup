"""Verifier and relayer keys: encrypted keystore files, with passwords from the OS credential store.

On Windows, `keyring` stores passwords in Credential Manager, which is DPAPI-encrypted to your Windows
login, so neither key nor password sits in plaintext on disk. An environment variable overrides it
(PTV_VERIFIER_PASSWORD / PTV_RELAYER_PASSWORD), mainly for tests.

    python -m ptverifier.keys new verifier C:/ptv/verifier.json   # creates key, prints its address
    python -m ptverifier.keys new relayer  C:/ptv/relayer.json
    python -m ptverifier.keys set-password verifier                # store an existing keystore's password
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from eth_account import Account

SERVICE = "ptverifier"
ROLES = ("verifier", "relayer")


def password(role: str) -> str:
    env = os.environ.get(f"PTV_{role.upper()}_PASSWORD")
    if env is not None:
        return env
    import keyring

    stored = keyring.get_password(SERVICE, role)
    if stored is None:
        raise SystemExit(f"no {role} password: run `python -m ptverifier.keys set-password {role}` or set PTV_{role.upper()}_PASSWORD")
    return stored


def load(role: str, keystore: Path) -> bytes:
    return bytes(Account.decrypt(json.loads(Path(keystore).read_text(encoding="utf-8")), password(role)))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ptverifier.keys", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("role", choices=ROLES)
    n.add_argument("keystore", type=Path)
    s = sub.add_parser("set-password")
    s.add_argument("role", choices=ROLES)
    a = p.parse_args(argv)

    import keyring

    if a.cmd == "new":
        if a.keystore.exists():
            raise SystemExit(f"{a.keystore} exists; refusing to overwrite a key")
        pw = getpass.getpass(f"new {a.role} keystore password: ")
        if pw != getpass.getpass("again: "):
            raise SystemExit("passwords differ")
        acct = Account.create()
        a.keystore.parent.mkdir(parents=True, exist_ok=True)
        a.keystore.write_text(json.dumps(Account.encrypt(acct.key, pw)), encoding="utf-8")
        keyring.set_password(SERVICE, a.role, pw)
        print(f"{a.role} address: {acct.address}")
        print(f"keystore: {a.keystore} (back it up; password stored in the OS credential store)")
    else:
        keyring.set_password(SERVICE, a.role, getpass.getpass(f"{a.role} keystore password: "))
        print("stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

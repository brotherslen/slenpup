"""Push the day's words to the phone through ntfy (https://ntfy.sh, or a self-hosted server).

The words aren't secret (anyone can derive them from the public on-chain challenge), so a third-party
relay is fine. A long random topic name just keeps strangers' messages out of your feed.
"""

from __future__ import annotations

import urllib.request


def send(server: str, topic: str, title: str, body: str, token: str | None = None, timeout_s: float = 20) -> None:
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Tags", "muscle")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"ntfy returned {resp.status}")


def words_message(day: int, session: str, words: dict[str, str], names: dict[str, str], deadline_local: str, tbd: list[str]) -> tuple[str, str]:
    title = f"Day {day}: {session}"
    lines = [f"{names[k]}: {w.upper()}" for k, w in words.items()]
    lines.append("")
    lines.append("One clip per exercise. Say the word, then hold the position 5 s (both sides for Y/T/W and split squats).")
    lines.append(f"Clips must reach the NUC by {deadline_local}.")
    if tbd:
        lines.append(f"Not verified yet (TBD): {', '.join(tbd)}")
    return title, "\n".join(lines)

"""The day's spoken words, derived from the on-chain challenge.

One word per exercise in the day's session. Saying it on camera before the position proves the clip was
recorded after the day's challenge existed, and it tells the verifier which exercise a clip is for, so
clips straight off the phone need no renaming.

The list avoids homophones (bear/bare, carrot/karat, cereal/serial), words with two common spellings,
compound words a transcriber might split, and anything that sounds like an exercise name.
"""

from __future__ import annotations

import hashlib
import re

WORDLIST: tuple[str, ...] = tuple(
    """
    tiger lion zebra giraffe elephant monkey gorilla panda koala kangaroo rabbit squirrel beaver otter
    badger raccoon wolf camel donkey pony sheep goat turkey chicken goose eagle falcon parrot penguin
    pelican flamingo owl robin sparrow raven pigeon swan turtle lizard snake frog shark dolphin octopus
    squid crab lobster shrimp oyster salmon spider beetle hamster ferret walrus buffalo bison cheetah
    leopard jaguar hippo rhino cobra gecko iguana toucan vulture
    apple banana cherry mango lemon melon grape peach kiwi coconut olive pepper potato tomato onion
    garlic pumpkin cucumber broccoli spinach celery radish turnip cabbage walnut almond peanut cookie
    muffin waffle pretzel noodle pizza burrito taco sandwich cheese butter honey pickle sausage bacon
    avocado pineapple papaya apricot biscuit cracker pasta salad vanilla cinnamon ginger mustard lettuce
    anchor arrow balloon basket battery blanket bucket button camera candle carpet castle chimney compass
    crayon cushion diamond drum envelope feather guitar hammer helmet kettle ladder lantern magnet marble
    mirror needle paddle pencil piano pillow pocket puzzle rocket saddle scissors shovel ticket trumpet
    umbrella violin wallet whistle window wrench zipper bottle hammock jacket sweater glove mitten scarf
    sandal slipper tractor bicycle scooter canoe kayak wagon helicopter submarine truck tunnel trophy
    telescope microscope banjo harp tuba flute robot sofa lamp clock spoon ribbon necklace crown sword
    shield barrel
    island volcano canyon glacier forest meadow river ocean valley cactus maple tulip daisy lily orchid
    clover acorn pebble crystal comet planet galaxy rainbow thunder tornado blizzard mountain jungle
    prairie lagoon iceberg cavern geyser avalanche hurricane
    """.split()
)


def _challenge_bytes(challenge: bytes | str) -> bytes:
    if isinstance(challenge, str):
        challenge = bytes.fromhex(challenge.removeprefix("0x"))
    if len(challenge) != 32:
        raise ValueError("challenge must be 32 bytes")
    return challenge


def day_words(challenge: bytes | str, exercise_keys: list[str]) -> dict[str, str]:
    """exercise key -> word, all distinct. Deterministic in (challenge, exercise key), so the NUC, the
    phone notification, and a later re-check all agree."""
    ch = _challenge_bytes(challenge)
    if len(exercise_keys) > len(WORDLIST):
        raise ValueError("more exercises than words")
    out: dict[str, str] = {}
    used: set[str] = set()
    for key in exercise_keys:
        counter = 0
        while True:
            digest = hashlib.sha256(ch + key.encode() + counter.to_bytes(4, "big")).digest()
            word = WORDLIST[int.from_bytes(digest, "big") % len(WORDLIST)]
            if word not in used:
                break
            counter += 1
        out[key] = word
        used.add(word)
    return out


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def find_word(spoken: list[tuple[float, str]], word: str) -> float | None:
    """Start time of the first time `word` was said. `spoken` is (start_s, text) per transcribed word.
    Accepts a plural and a word split across two transcribed tokens ("pine apple")."""
    flat: list[tuple[float, str]] = [(t, tok) for t, text in spoken for tok in _tokens(text)]
    for i, (t, tok) in enumerate(flat):
        if tok in (word, word + "s", word + "es"):
            return t
        if i + 1 < len(flat) and tok + flat[i + 1][1] == word:
            return t
    return None

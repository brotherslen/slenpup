import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from ptverifier import calibrate, chain, daily, notify
from ptverifier.config import ConfigError, load_config
from ptverifier.exercises import load_catalog
from ptverifier.session import verify_day
from ptverifier.words import WORDLIST, day_words, find_word

from .synthetic import concat, posture_track

REPO = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO / "verifier" / "exercises.yaml"
CH1 = bytes(range(32))
CH2 = bytes(range(1, 33))


# --------------------------------------------------------------------------------------- words


def test_wordlist_is_clean():
    assert len(WORDLIST) == len(set(WORDLIST)) >= 200
    assert all(w.isalpha() and w.islower() for w in WORDLIST)
    catalog = load_catalog(CATALOG_PATH)
    names = " ".join(e.name.lower() for e in catalog.exercises.values())
    assert not [w for w in WORDLIST if w in names.split()]
    for homophone in ("bear", "carrot", "cereal", "horse", "plum", "deer", "flour", "desert", "boulder", "bridge"):
        assert homophone not in WORDLIST


def test_day_words_deterministic_distinct_and_fresh():
    keys = ["wall_slides", "serratus_pushup_plus", "prone_y", "prone_t", "prone_w", "bear_hold"]
    a = day_words(CH1, keys)
    assert a == day_words("0x" + CH1.hex(), keys)
    assert len(set(a.values())) == len(keys)
    assert a != day_words(CH2, keys)
    with pytest.raises(ValueError):
        day_words(b"short", keys)


def test_find_word():
    spoken = [(0.5, " Okay,"), (1.0, " Tiger!"), (4.0, " pine"), (4.3, " apple"), (6.0, " robins")]
    assert find_word(spoken, "tiger") == 1.0
    assert find_word(spoken, "pineapple") == 4.0
    assert find_word(spoken, "robin") == 6.0
    assert find_word(spoken, "zebra") is None


# --------------------------------------------------------------------------------- config


def _config(tmp_path, **over):
    data = {
        "rpc_url": "http://127.0.0.1:1",
        "contract": "0x" + "11" * 20,
        "schedule": "AWR-B",
        "sessions_dir": str(tmp_path / "sessions"),
        "ntfy": {"server": "http://127.0.0.1:1", "topic": "t"},
    } | over
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_config_schedule(tmp_path):
    cfg = load_config(_config(tmp_path))
    assert cfg.days == ["A", "W", "R", "B"]
    assert cfg.bitmap == 0b1011
    assert [cfg.session_for(d) for d in range(5)] == ["PT-A", "Walk-A", None, "PT-B", None]
    with pytest.raises(ConfigError, match="unknown letters"):
        load_config(_config(tmp_path, schedule="AXR"))
    with pytest.raises(ConfigError, match="topic"):
        load_config(_config(tmp_path, ntfy={"server": "x"}))


def test_example_config_loads():
    cfg = load_config(REPO / "verifier" / "config.example.yaml")
    assert cfg.whisper_model == "small.en"


# ----------------------------------------------------------------------- session verification


@pytest.fixture(scope="module")
def calibrated(tmp_path_factory):
    """exercises.yaml with bands from synthetic calibration clips, plus a TEST session."""
    catalog = load_catalog(CATALOG_PATH)
    data = yaml.safe_load(CATALOG_PATH.read_text())
    for key, posture, sides in (("wall_slides", "wall_slide", ["left"]), ("bear_hold", "bear", ["left"]), ("prone_y", "prone_y", ["left", "right"])):
        ex = catalog.get(key)
        bands = {}
        for side in sides:
            result = calibrate.analyse(posture_track([(posture, 8)], near=side), ex, side)
            bands[side] = {k: list(v) for k, v in calibrate.pooled_bands(ex, [result]).items()}
        data["exercises"][key]["bands"] = bands
    data["sessions"]["TEST"] = ["wall_slides", "prone_y", "bear_hold", "glute_bridge"]
    data["sessions"]["PT-A"] = ["wall_slides", "prone_y", "bear_hold"]  # what the Anvil test seeds
    path = tmp_path_factory.mktemp("cat") / "exercises.yaml"
    path.write_text(yaml.safe_dump(data))
    return load_catalog(path)


class FakeTranscriber:
    def __init__(self, spoken):
        self.spoken = spoken

    def words(self, video):
        return self.spoken[Path(video).name]


def _clips(tmp_path, words, *, prone_one_side=False, late_word=False, splice=False):
    """Phone-named clips and what's said/seen in each."""
    tracks, spoken = {}, {}
    tracks["VID_001.mp4"] = posture_track([("standing", 2), ("wall_slide", 7)])
    spoken["VID_001.mp4"] = [(0.5, words["wall_slides"]), (12.0, "done")]
    prone = [posture_track([("prone_y", 7)], near="left")]
    if not prone_one_side:
        prone.append(posture_track([("standing", 1), ("prone_y", 7)], near="right"))
    tracks["VID_002.mp4"] = concat(*prone)
    spoken["VID_002.mp4"] = [(0.2, words["prone_y"])]
    tracks["VID_003.mp4"] = posture_track([("standing", 2), ("bear", 7)])
    spoken["VID_003.mp4"] = [(5.0 if late_word else 0.4, words["bear_hold"])]
    if splice:
        t = tracks["VID_003.mp4"]
        t.t_ms[len(t.t_ms) // 2 :] += 2000
    tracks["VID_999.mp4"] = posture_track([("standing", 3)])
    spoken["VID_999.mp4"] = [(0.5, "hello")]
    for name in tracks:
        (tmp_path / name).write_bytes(name.encode())
    return [tmp_path / n for n in tracks], FakeTranscriber(spoken), lambda v: tracks[Path(v).name]


def _words(catalog, challenge=CH1):
    return day_words(challenge, [k for k in catalog.sessions["TEST"] if k in catalog.exercises])


def test_verify_day_passes(tmp_path, calibrated):
    clips, tr, ext = _clips(tmp_path, _words(calibrated))
    r = verify_day(3, CH1, "TEST", calibrated, clips, tr, ext, {})
    assert r["passed"] and r["score"] == 3, json.dumps(r, indent=1)
    assert r["unmatched_clips"] == ["VID_999.mp4"]
    assert r["not_verified_tbd"] == ["glute_bridge"]
    assert r["exercises"]["prone_y"]["clip"]["held_s"]["left"] >= 5
    assert r["exercises"]["prone_y"]["clip"]["held_s"]["right"] >= 5


def test_verify_day_failures(tmp_path, calibrated):
    words = _words(calibrated)
    clips, tr, ext = _clips(tmp_path, words, prone_one_side=True, late_word=True)
    r = verify_day(3, CH1, "TEST", calibrated, clips, tr, ext, {})
    assert not r["passed"] and r["score"] == 1
    assert "right held" in r["exercises"]["prone_y"]["reason"]
    assert "not held" in r["exercises"]["bear_hold"]["reason"]  # position was before the word


def test_yesterdays_words_dont_count(tmp_path, calibrated):
    clips, tr, ext = _clips(tmp_path, _words(calibrated, CH2))
    r = verify_day(3, CH1, "TEST", calibrated, clips, tr, ext, {})
    assert r["score"] == 0 and len(r["unmatched_clips"]) == 4


def test_spliced_and_reused_clips_rejected(tmp_path, calibrated):
    clips, tr, ext = _clips(tmp_path, _words(calibrated), splice=True)
    from ptverifier.pose import sha256_file

    used = {sha256_file(tmp_path / "VID_001.mp4"): 2}
    r = verify_day(3, CH1, "TEST", calibrated, clips, tr, ext, used)
    assert "already counted for day 2" in r["exercises"]["wall_slides"]["reason"]
    assert "frame gap" in r["exercises"]["bear_hold"]["reason"]


def test_all_tbd_session_cannot_pass(tmp_path, calibrated):
    r = verify_day(3, CH1, "PT-B", calibrated, [], FakeTranscriber({}), None, {})
    assert not r["passed"] and "no verifiable exercises" in r["note"]


def test_words_message():
    title, body = notify.words_message(4, "PT-A", {"wall_slides": "tiger"}, {"wall_slides": "Wall slides"}, "Tue 10:00 CDT", ["glute_bridge"])
    assert title == "Day 4: PT-A"
    assert "Wall slides: TIGER" in body and "Tue 10:00 CDT" in body and "glute_bridge" in body


# ----------------------------------------------------------------- end to end on a local chain


class _Ntfy(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        _Ntfy.received.append({"path": self.path, "title": self.headers["Title"], "body": body})
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _have_foundry():
    return shutil.which("anvil") and shutil.which("forge") and shutil.which("cast")


@pytest.mark.skipif(not _have_foundry(), reason="needs anvil, forge and cast on PATH")
def test_seed_notify_verify_on_anvil(tmp_path, calibrated, monkeypatch):
    port = 8600 + os.getpid() % 300
    rpc = f"http://127.0.0.1:{port}"
    anvil = subprocess.Popen(["anvil", "--port", str(port), "--silent"])
    server = HTTPServer(("127.0.0.1", 0), _Ntfy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for _ in range(50):
            if subprocess.run(["cast", "chain-id", "--rpc-url", rpc], capture_output=True).returncode == 0:
                break
            time.sleep(0.2)
        mnemonic = "test test test test test test test test test test test junk"
        key = lambda i: subprocess.run(["cast", "wallet", "private-key", "--mnemonic", mnemonic, "--mnemonic-index", str(i)], capture_output=True, text=True, check=True).stdout.strip()  # noqa: E731
        addr = lambda k: subprocess.run(["cast", "wallet", "address", k], capture_output=True, text=True, check=True).stdout.strip()  # noqa: E731
        deployer, owner, benef, verifier, relayer = (key(i) for i in range(5))

        def create(*args):
            out = subprocess.run(["forge", "create", "--rpc-url", rpc, "--private-key", deployer, "--broadcast", *args], cwd=REPO, capture_output=True, text=True)
            if out.returncode != 0:
                pytest.skip(f"forge create failed (compiler not available?): {out.stderr[-300:]}")
            return next(line.split()[-1] for line in out.stdout.splitlines() if "Deployed to:" in line)

        usdc = create("test/mocks/MockUSDC.sol:MockUSDC")
        now = int(subprocess.run(["cast", "block", "latest", "--field", "timestamp", "--rpc-url", rpc], capture_output=True, text=True).stdout)
        start = (now // 3600 + 2) * 3600
        contract = create("src/PTCommitment.sol:PTCommitment", "--constructor-args", addr(owner), addr(benef), addr(verifier), usdc, str(start), "3", "0x3", "1000000", "21600")
        send = lambda pk, *a: subprocess.run(["cast", "send", "--rpc-url", rpc, "--private-key", pk, *a], capture_output=True, check=True)  # noqa: E731
        send(deployer, usdc, "mint(address,uint256)", addr(owner), "2000000")
        send(owner, usdc, "approve(address,uint256)", contract, "2000000")
        send(owner, contract, "fund()")
        subprocess.run(["cast", "rpc", "--rpc-url", rpc, "evm_setNextBlockTimestamp", str(start + 300)], capture_output=True, check=True)
        subprocess.run(["cast", "rpc", "--rpc-url", rpc, "evm_mine"], capture_output=True, check=True)

        from eth_account import Account

        keystore = tmp_path / "relayer.json"
        keystore.write_text(json.dumps(Account.encrypt(bytes.fromhex(relayer[2:]), "pw")))
        monkeypatch.setenv("PTV_RELAYER_PASSWORD", "pw")
        cfg = load_config(_config(tmp_path, rpc_url=rpc, contract=contract, schedule="AWR",
                                  ntfy={"server": f"http://127.0.0.1:{server.server_port}", "topic": "ptv-test"},
                                  relayer_keystore=str(keystore)))
        c = chain.Commitment(cfg.rpc_url, cfg.contract)
        daily._check_schedule(cfg, c)
        assert c.current_day() == 0

        daily.cmd_seed(cfg, calibrated, c, 0, finalized=False)
        info = c.day(0)
        assert info.state == chain.SEEDED
        words = day_words(info.challenge, ["wall_slides", "prone_y", "bear_hold"])
        msg = _Ntfy.received[-1]
        assert msg["path"] == "/ptv-test" and msg["title"] == "Day 0: PT-A"
        assert all(w.upper() in msg["body"] for w in words.values())

        daily.cmd_seed(cfg, calibrated, c, 0, finalized=False)  # second run: no new tx, words resent
        assert c.day(0).state == chain.SEEDED

        inbox = cfg.sessions_dir / "inbox"
        inbox.mkdir(parents=True)
        clip_paths, tr, ext = _clips(inbox, words)
        reports = daily.cmd_verify(cfg, calibrated, c, [0], tr, ext)
        assert reports[0]["passed"], json.dumps(reports[0], indent=1)
        assert (cfg.sessions_dir / "days" / "day-000" / "VID_001.mp4").exists()
        assert (inbox / "VID_999.mp4").exists()  # unmatched clip stays in the inbox
        ledger = json.loads((cfg.sessions_dir / "used_videos.json").read_text())
        assert sorted(ledger.values()) == [0, 0, 0]
    finally:
        server.shutdown()
        anvil.terminate()

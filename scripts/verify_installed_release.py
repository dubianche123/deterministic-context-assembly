"""Exercise a wheel installed outside the checkout, without model credentials."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urljoin, urlsplit
from urllib.request import build_opener, ProxyHandler, Request


def verify(source_root, expected_version):
    import norn
    from norn.benchmark import default_journal
    from norn.cli import doctor_report
    from norn.core import web_root
    from norn.server import make_server
    installed = Path(norn.__file__).resolve()
    assert not installed.is_relative_to(source_root.resolve()), "Use an installed wheel, not the checkout"
    assert not Path.cwd().resolve().is_relative_to(source_root.resolve()), "Run outside the checkout"
    assert norn.__version__ == expected_version
    doctor = doctor_report()
    assert doctor["ok"] and doctor["network_used"] is False
    for name in ("index.html", "app.js", "style.css"):
        assert (web_root() / name).read_bytes() == (source_root / "the-norn-machine/frontend" / name).read_bytes()
    server = make_server(port=0, provider="mock", host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    opener = build_opener(ProxyHandler({}))
    def get(path):
        assert path.startswith("/") and not path.startswith("//")
        with opener.open(base + path, timeout=15) as response:
            return response.read(), response.headers
    def post(path, data):
        request = Request(base + path, data=json.dumps(data, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json", "Origin": base}, method="POST")
        with opener.open(request, timeout=15) as response:
            return json.load(response)
    try:
        health = json.loads(get("/api/health")[0])
        assert health["offline"] and health["provider"] == "mock"
        assert b"X-Ray" in get("/")[0]
        manifest = json.loads(get("/data/cards.json")[0])
        assert len(manifest) == 256
        for card in manifest:
            raw, headers = get(urlsplit(urljoin(base + "/", card["url"])).path)
            assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
            assert headers.get_content_type() == "image/webp"
        journal = default_journal()
        partial = {"schema_version": journal["schema_version"], "events": deepcopy(journal["events"][:4])}
        preview = post("/api/preview", {"journal": partial})
        assert preview["profile"]["schema_version"] == "norn.preview.v5" and not preview["complete"]
        assert preview["profile"]["numerical_contract"] == "norn.numeric.decimal34.v1" and not preview["calls"]
        complete = post("/api/preview", {"journal": journal})
        assert complete["complete"] and not complete["calls"]
        assert complete["profile"]["schema_version"] == "norn.profile.v5"
        assert complete["preference"]["feature_count"] == 12
        trajectory = post("/api/trajectory", {"journal": journal})
        assert trajectory["rounds"] == len(trajectory["states"]) - 1 and len(trajectory["states"][-1]["scores"]) == 256
        positions = lambda view: [(point["card_id"], point["x"], point["y"]) for point in view["preference_map"]["points"]]
        assert positions(preview) == positions(complete) and len(positions(complete)) == 256
        payload = {"journal": journal, "request_id": "installed-release-reading"}
        reading = post("/api/analyze", payload)
        assert post("/api/analyze", payload) == reading
        assert reading["status"] == "ok" and reading["reading"]
        key = reading["session_id"]
        first = json.loads(get("/api/sessions/" + key + "/inspect")[0])
        assert first["profile_hash"] == complete["profile_hash"] == reading["profile_hash"]
        original_profile = deepcopy(first["profile"])
        original_prompt = first["calls"][0]["prompt"]
        history = deepcopy(original_prompt["messages"]) + [{"role": "assistant", "content": reading["reading"]}]
        for number, question in enumerate(("你为什么这样理解我的选择？", "这次撤回意味着什么？", "下一步我可以做什么？"), 1):
            payload = {"session_id": key, "user_message": question, "turn_id": "installed-release-turn-" + str(number)}
            reply = post("/api/dialogue", payload)
            assert post("/api/dialogue", payload) == reply
            assert reply["status"] == "ok" and reply["turn_count"] == number
            last = json.loads(get("/api/sessions/" + key + "/inspect")[0])
            assert last["profile"] == original_profile and len(last["calls"]) == number + 1
            current = last["calls"][-1]["prompt"]
            assert current["system"] == original_prompt["system"] and current["messages"][:-1] == history
            history = deepcopy(current["messages"]) + [{"role": "assistant", "content": reply["reply"]}]
        assert reply["ended"]
        compact = json.loads(get("/api/sessions/" + key + "/inspect?compact=1")[0])
        assert compact["profile_hash"] == original_profile["profile_hash"]
        bundle = json.loads(get("/api/sessions/" + key + "/export")[0])
        replay = post("/api/replay", bundle)
        assert replay["profile"] == original_profile
        # Scoring v5: the effective guard is part of the replayed result.
        assert replay["coords_avg"] is None and replay["reconstructed_coords"] is None
        learned = bundle["profile"]["analysis"]["preference"]
        assert learned["model_version"] == "norn.choice.guarded-mnl.v1"
        assert len(learned["recommendations"]) == (6 if learned["effective"]["active"] else 0)
        frozen = json.loads(get("/api/sessions/" + key + "/trajectory")[0])
        assert frozen["source"] == "session" and frozen["profile"]["profile_hash"] == original_profile["profile_hash"]
        legacy = json.loads((source_root / "tests/fixtures/legacy-profile-v1.json").read_text())
        assert post("/api/replay", legacy)["profile"] == legacy["profile"]
        with tempfile.TemporaryDirectory(prefix="norn-release-learning-") as directory:
            source, checkpoint = Path(directory) / "journal.json", Path(directory) / "checkpoint.json"
            source.write_text(json.dumps(journal), encoding="utf-8")
            def cli(*arguments):
                return json.loads(subprocess.run([sys.executable, "-m", "norn", *arguments],
                    check=True, capture_output=True, text=True).stdout)
            learned = cli("learn", str(source), "--output", str(checkpoint), "--json")
            assert learned["model_version"] == "norn.preference.linear-bt.v2" and learned["pair_count"] == 48
            ranked = cli("rank", str(checkpoint), "--recording", str(source), "--top", "8", "--json")
            assert ranked["verified"] and ranked["source_replayed"] and len(ranked["predictions"]) == 8
        final_health = json.loads(get("/api/health")[0])
        assert final_health["limits"]["model_calls_reserved"] == 0
        return {"schema_version": "norn.installed-release-verification.v1", "version": norn.__version__,
                "python": sys.version.split()[0], "installed_outside_checkout": True,
                "doctor_ok": True, "http_offline_images_verified": len(manifest),
                "installed_frontend_matches_source": True, "partial_and_complete_preview": True,
                "analysis_and_dialogue_retry_idempotent": True, "successful_followups": 3,
                "native_history_and_frozen_profile_verified": True, "compact_inspection": True,
                "profile5_export_and_exact_replay": True, "legacy_profile1_exact_replay": True,
                "model2_source_replayed_cli_rank": True, "session_trajectory": True,
                "feature_count": 12, "fixed_projection_points": 256,
                "profile_hash": original_profile["profile_hash"],
                "numerical_contract": original_profile["numerical_contract"], "real_model_calls": 0}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--expected-version", default="0.8.0")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.source_root, args.expected_version)
    if args.wheel:
        result.update(wheel_filename=args.wheel.name, wheel_bytes=args.wheel.stat().st_size,
                      wheel_sha256=hashlib.sha256(args.wheel.read_bytes()).hexdigest())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

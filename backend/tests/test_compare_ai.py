"""The comparison script, with fake providers: no network, no cost."""

import argparse
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "compare_ai.py"
spec = importlib.util.spec_from_file_location("compare_ai", SCRIPT)
compare_ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_ai)


class Fake:
    """An AI provider answering from a function of the question."""

    def __init__(self, name, answer):
        self.name, self.model, self.answer = name, "fake", answer

    def ask_json(self, system, content, schema):
        return self.answer(system)


def sets_answer(*set_nums):
    return {"candidates": [{"set_num": s, "name": "", "confidence": 0.5, "evidence": ""} for s in set_nums]}


def parts_answer(*part_nums):
    return {"candidates": [{"part_num": p, "confidence": 0.5, "reason": ""} for p in part_nums]}


@pytest.fixture
def env_file(tmp_path, db):
    path = tmp_path / "test.env"
    path.write_text(f"DATA_DIR={tmp_path}\n")
    return str(path)


def image(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((50, 50, 3), 128, np.uint8))
    return path.name


def test_export_takes_confirmed_unsure_bricks(db, tmp_path, env_file):
    project = db.execute("INSERT INTO projects (name, set_num) VALUES ('p', '1234-1')").lastrowid
    photo = db.execute("INSERT INTO photos (project_id, kind, path) VALUES (?, 'pile', 'x')", (project,)).lastrowid
    for crop, score, status, part in [
        ("crops/unsure.jpg", 0.2, "confirmed", "3003"),
        ("crops/sure.jpg", 0.9, "confirmed", "3001"),
        ("crops/pending.jpg", 0.2, "pending", None),
    ]:
        image(tmp_path / crop)
        candidates = json.dumps([{"part_num": "3001", "score": score, "source": "brickognize"}])
        detection = db.execute(
            "INSERT INTO detections (photo_id, crop_path, candidates, status) VALUES (?, ?, ?, ?)",
            (photo, crop, candidates, status),
        ).lastrowid
        if part:
            db.execute(
                "INSERT INTO project_parts (project_id, part_num, color_id, quantity, source, detection_id)"
                " VALUES (?, ?, 5, 1, 'found', ?)",
                (project, part, detection),
            )
    db.commit()

    cases_dir = tmp_path / "cases"
    args = argparse.Namespace(folder=cases_dir, limit=30, env_file=env_file)
    compare_ai.export(args)
    compare_ai.export(args)  # a second export adds nothing twice

    cases = json.loads((cases_dir / "cases.json").read_text())
    assert cases["sets"] == []
    [case] = cases["parts"]
    assert case["set_num"] == "1234-1" and case["expected"] == "3003"
    assert (cases_dir / case["crop"]).exists()


def test_run_scores_both_jobs(db, tmp_path, env_file, monkeypatch, capsys):
    cases_dir = tmp_path / "cases"
    cases = {
        "sets": [
            {"photos": [image(cases_dir / "a.jpg")], "expected": "1234"},
            {"photos": [image(cases_dir / "b.jpg")], "expected": "5678-1"},
        ],
        "parts": [{"crop": image(cases_dir / "c.jpg"), "set_num": "1234-1", "expected": "3003"}],
    }
    (cases_dir / "cases.json").write_text(json.dumps(cases))

    def good(system):
        return sets_answer("1234-1", "5678-1") if "LEGO sets" in system else parts_answer("3003")

    def weak(system):  # unknown set first (dropped as in the app), part only second
        return sets_answer("9999-1", "5678-1") if "LEGO sets" in system else parts_answer("3001", "3003")

    usage = compare_ai.Usage
    monkeypatch.setattr(compare_ai, "providers", lambda settings, wanted: {
        "infomaniak": (Fake("infomaniak", weak), usage()),
        "claude": (Fake("claude", good), usage()),
    })
    details = tmp_path / "details.json"
    compare_ai.run(argparse.Namespace(
        folder=cases_dir, env_file=env_file, providers=["infomaniak", "claude"], jobs=["sets", "parts"],
        price=[compare_ai.parse_price("claude=1,2")], details=details,
    ))

    rows = {tuple(line.split()[:2]): line.split()[2:] for line in capsys.readouterr().out.splitlines()
            if line.startswith(("sets ", "parts "))}
    assert rows[("sets", "infomaniak")][:4] == ["2", "50%", "50%", "0"]  # 1234 missing; 5678 first
    assert rows[("sets", "claude")][:4] == ["2", "50%", "100%", "0"]
    assert rows[("parts", "infomaniak")][:4] == ["1", "0%", "100%", "0"]
    assert rows[("parts", "claude")][:4] == ["1", "100%", "100%", "0"]
    ranks = [(d["job"], d["provider"], d["rank"]) for d in json.loads(details.read_text())]
    assert ("parts", "infomaniak", 2) in ranks


def test_bad_price_is_a_usage_error():
    with pytest.raises(argparse.ArgumentTypeError):
        compare_ai.parse_price("claude=5")

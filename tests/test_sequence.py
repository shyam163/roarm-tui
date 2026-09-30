import json

import pytest

from roarm.protocol import HOME, Pose
from roarm.sequence import Point, Recorder, Sequence, list_sequences, load, safe_name, save


def wp_seq():
    return Sequence("pick", "waypoints", [Point(HOME, dwell=0.5), Point(HOME.with_joint("base", 1.0), dwell=1.0)])


def test_round_trip(tmp_path):
    seq = wp_seq()
    path = save(seq, tmp_path)
    assert path == tmp_path / "pick.json"
    loaded = load(path)
    assert loaded.name == "pick" and loaded.kind == "waypoints"
    assert [p.dwell for p in loaded.points] == [0.5, 1.0]
    assert loaded.points[1].pose.base == pytest.approx(1.0)


def test_file_format(tmp_path):
    data = json.loads(save(wp_seq(), tmp_path).read_text())
    assert data["version"] == 1
    assert data["points"][0] == {"b": 0.0, "s": 0.0, "e": 1.57, "h": 3.14, "dwell": 0.5}


def test_trajectory_round_trip(tmp_path):
    seq = Sequence("rec", "trajectory", [Point(HOME, dwell=0.0, t=0.0), Point(HOME, dwell=0.0, t=0.05)])
    loaded = load(save(seq, tmp_path))
    assert [p.t for p in loaded.points] == [0.0, 0.05]
    assert "dwell" not in json.loads((tmp_path / "rec.json").read_text())["points"][0]


@pytest.mark.parametrize("name,expected", [
    ("pick", "pick"),
    ("my seq!", "my_seq"),
    ("../../etc/x", "etc_x"),
    ("   ", "sequence"),
    ("", "sequence"),
])
def test_safe_name(name, expected):
    assert safe_name(name) == expected


def test_save_sanitizes_name(tmp_path):
    seq = wp_seq()
    seq.name = "../../evil"
    path = save(seq, tmp_path)
    assert path.parent == tmp_path
    assert path.name == "evil.json"


def test_from_dict_clamps_out_of_range():
    seq = Sequence.from_dict({"version": 1, "name": "x", "kind": "waypoints",
                              "points": [{"b": 99, "s": 0, "e": 1, "h": 2, "dwell": -3}]})
    assert seq.points[0].pose.base == 3.14
    assert seq.points[0].dwell == 0.0


@pytest.mark.parametrize("content", [
    "not json",
    json.dumps([1, 2]),
    json.dumps({"version": 2, "kind": "waypoints", "points": []}),
    json.dumps({"version": 1, "kind": "dance", "points": []}),
    json.dumps({"version": 1, "kind": "waypoints", "points": "nope"}),
    json.dumps({"version": 1, "kind": "waypoints", "points": [{"b": 0}]}),
    json.dumps({"version": 1, "kind": "waypoints", "points": [{"b": "x", "s": 0, "e": 0, "h": 2}]}),
    '{"version": 1, "kind": "waypoints", "points": [{"b": NaN, "s": 0, "e": 0, "h": 2}]}',
    json.dumps({"version": 1, "kind": "trajectory", "points": [{"b": 0, "s": 0, "e": 0, "h": 2}]}),
    json.dumps({"version": 1, "kind": "trajectory", "points": [
        {"b": 0, "s": 0, "e": 0, "h": 2, "t": 1.0}, {"b": 0, "s": 0, "e": 0, "h": 2, "t": 0.5}]}),
    '{"version": 1, "kind": "trajectory", "points": [{"b": 0, "s": 0, "e": 0, "h": 2, "t": NaN}]}',
    '{"version": 1, "kind": "trajectory", "points": [{"b": 0, "s": 0, "e": 0, "h": 2, "t": Infinity}]}',
    '{"version": 1, "kind": "waypoints", "points": [{"b": 0, "s": 0, "e": 0, "h": 2, "dwell": NaN}]}',
    '{"version": 1, "kind": "waypoints", "points": [{"b": 0, "s": 0, "e": 0, "h": 2, "dwell": Infinity}]}',
])
def test_load_rejects_bad_files(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load(path)


def test_load_missing_file_is_value_error(tmp_path):
    with pytest.raises(ValueError):
        load(tmp_path / "missing.json")


def test_list_sequences(tmp_path):
    assert list_sequences(tmp_path / "nope") == []
    save(Sequence("b"), tmp_path)
    save(Sequence("a"), tmp_path)
    assert [p.name for p in list_sequences(tmp_path)] == ["a.json", "b.json"]


def test_recorder_rate_limits_and_offsets_time():
    rec = Recorder(interval=0.04)
    rec.add(10.00, HOME)
    rec.add(10.01, HOME)          # too soon, dropped
    rec.add(10.05, HOME.with_joint("base", 0.5))
    seq = rec.to_sequence("r")
    assert seq.kind == "trajectory"
    assert [p.t for p in seq.points] == [0.0, pytest.approx(0.05)]
    assert seq.points[1].pose.base == 0.5

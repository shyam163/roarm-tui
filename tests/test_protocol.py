import json
import math

import pytest

from roarm import protocol as P


def test_clamp_joint_limits():
    assert P.clamp_joint("elbow", -1.0) == 0.0
    assert P.clamp_joint("shoulder", 2.0) == 1.57
    assert P.clamp_joint("base", 0.5) == 0.5
    with pytest.raises(ValueError):
        P.clamp_joint("base", float("nan"))


def test_pose_defaults_are_home():
    assert P.HOME == P.Pose(0.0, 0.0, 1.57, 3.14)


def test_pose_with_joint_clamps():
    p = P.HOME.with_joint("hand", 0.0)
    assert p.hand == P.GRIP_OPEN
    assert p.base == P.HOME.base


def test_pose_dict_round_trip_and_clamp():
    p = P.Pose(0.1, -0.2, 1.0, 2.0)
    assert P.Pose.from_dict(p.to_dict()) == p
    assert P.Pose.from_dict({"b": 9, "s": 0, "e": 1, "h": 2}).base == 3.14
    with pytest.raises(KeyError):
        P.Pose.from_dict({"b": 0})


def test_max_diff():
    assert P.HOME.max_diff(P.HOME.with_joint("elbow", 1.0)) == pytest.approx(0.57)


def test_cmd_joints_is_clamped_and_rounded():
    cmd = P.cmd_joints(P.Pose(5.0, 0.123456789, 1.0, 3.0), spd=300, acc=5)
    assert cmd == {"T": 102, "base": 3.14, "shoulder": 0.1235, "elbow": 1.0, "hand": 3.0, "spd": 300, "acc": 5}


def test_simple_commands():
    assert P.cmd_home() == {"T": 100}
    assert P.cmd_feedback() == {"T": 105}
    assert P.cmd_torque(False) == {"T": 210, "cmd": 0}
    assert P.cmd_torque(True) == {"T": 210, "cmd": 1}
    assert P.cmd_led(999) == {"T": 114, "led": 255}
    assert P.cmd_joint("elbow", 4.0) == {"T": 101, "joint": 3, "rad": 3.14, "spd": 0, "acc": 10}
    assert P.cmd_gripper(0.0) == {"T": 106, "cmd": 1.08, "spd": 0, "acc": 0}
    assert P.cmd_mac() == {"T": 302}
    assert P.cmd_wifi_info() == {"T": 405}


def test_encode_is_compact_json_line():
    assert P.encode({"T": 105}) == b'{"T":105}\n'


def test_parse_line():
    assert P.parse_line('  {"T": 105}\r') == {"T": 105}
    assert P.parse_line("Moving BASE_JOINT to initPos.") is None
    assert P.parse_line("{broken") is None
    assert P.parse_line("[1, 2]") is None


FEEDBACK = ('{"T":1051,"x":315.236647,"y":0,"z":221.8803053,"b":0,"s":0.023009712,'
            '"e":1.592272058,"t":3.140058673,"torB":0,"torS":76,"torE":72,"torH":0}')


def test_parse_feedback_this_firmware():
    st = P.parse_feedback(json.loads(FEEDBACK), 12.5)
    assert st.pose == P.Pose(0.0, 0.023009712, 1.592272058, 3.140058673)
    assert (st.x, st.y, st.z) == (315.236647, 0.0, 221.8803053)
    assert st.loads == {"base": 0, "shoulder": 76, "elbow": 72, "hand": 0}
    assert st.timestamp == 12.5
    assert st.torque is None and st.voltage is None


def test_parse_feedback_optional_fields():
    msg = json.loads(FEEDBACK)
    msg.update(torswitchB=1, torswitchS=1, torswitchE=0, torswitchH=1, v=1211)
    st = P.parse_feedback(msg, 0.0)
    assert st.torque == {"base": True, "shoulder": True, "elbow": False, "hand": True}
    assert st.voltage == pytest.approx(12.11)


def test_parse_feedback_rejects_other_messages():
    assert P.parse_feedback({"T": 105}, 0.0) is None
    assert P.parse_feedback({"T": 1051, "x": 1}, 0.0) is None
    assert P.parse_feedback({"T": 1051, "x": "a", "y": 0, "z": 0, "b": 0, "s": 0, "e": 0, "t": 0}, 0.0) is None


def test_forward_kinematics_home_matches_arm():
    x, y, z = P.forward_kinematics(P.HOME)
    assert x == pytest.approx(316.0, abs=1)
    assert y == pytest.approx(0.0, abs=1e-6)
    assert z == pytest.approx(238.0, abs=1)
    x, y, _ = P.forward_kinematics(P.HOME.with_joint("base", math.pi / 2))
    assert x == pytest.approx(0.0, abs=1e-6) and y == pytest.approx(316.0, abs=1)


def test_planar_points():
    pts = P.planar_points(P.HOME)
    assert pts[0] == (0.0, 0.0)
    assert pts[1] == pytest.approx((0.0, 238.0))
    assert pts[2] == pytest.approx((316.0, 238.0), abs=0.5)

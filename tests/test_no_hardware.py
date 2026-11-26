"""Static boundary check: no hardware, DDS, network or motion execution.

Only executable and declarative sources are scanned; documentation may name
the robot and the boundaries. The package is ROS-free and transport-free in
v0.1.0, so a network client token is also a failure here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / "src", ROOT / "scripts", ROOT / "recipes")
SUFFIXES = {".py", ".yaml", ".yml", ".sh", ".toml"}

FORBIDDEN = (
    "rclpy",
    "rospy",
    "ros2",
    "/dev/",
    "pyserial",
    "serial.serial",
    "socketcan",
    "can0",
    "pigpio",
    "rpi.gpio",
    "gpiozero",
    "gpiod",
    "cv2.videocapture",
    "picamera",
    "ros_robot_controller",
    "set_motor_speeds",
    "motorsspeedcontrol",
    "executemotion",
    "execute_motion",
    "/move_action",
    "/cmd_vel",
    "/joint_states",
    "10.42.0.218",
    "paramiko",
    "subprocess",
    "os.system",
    "popen",
    "requests",
    "urllib",
    "httpx",
    "aiohttp",
    "http.client",
    "socket",
)

ARTIFACT_SUFFIXES = {
    ".pt",
    ".onnx",
    ".engine",
    ".bag",
    ".mcap",
    ".db3",
    ".mp4",
    ".avi",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".env",
}


def _source_files():
    for directory in SCAN_DIRS:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in SUFFIXES:
                yield path


def test_no_hardware_dds_network_or_motion_tokens() -> None:
    problems = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in FORBIDDEN:
            if token in text:
                problems.append(f"{path.relative_to(ROOT)}: {token}")
    assert not problems, "forbidden hardware/DDS/network tokens found:\n" + "\n".join(
        problems
    )


def test_no_model_weights_or_recordings() -> None:
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.relative_to(ROOT).parts:
            continue
        if path.suffix in ARTIFACT_SUFFIXES:
            found.append(str(path.relative_to(ROOT)))
    assert not found, "model weights or recordings found:\n" + "\n".join(found)


def test_manifest_has_no_runtime_surface() -> None:
    manifest = yaml.safe_load(
        (ROOT / "capability.yaml").read_text(encoding="utf-8")
    )
    assert manifest["hardware_access"] == "none"
    assert manifest["runtime"] == {
        "ros_domains": [],
        "publishes": [],
        "subscribes": [],
    }


def test_gitignore_covers_runtime_artifacts() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        "datasets/",
        "models/",
        "data/",
        "runs/",
        "*.pt",
        "*.onnx",
        "*.engine",
        "*.bag",
        "*.mcap",
        "*.db",
        "*.sqlite",
        "*.env",
    ):
        assert pattern in ignored, f".gitignore missing {pattern}"

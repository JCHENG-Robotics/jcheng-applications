"""Static boundary check: no hardware, DDS, network or motion execution.

Only executable and declarative sources are scanned; documentation may name
the robot and the boundaries. The package is ROS-free. Two adapters may
import ``http.client``: the M2 read-only ``transport.py`` and the M3
``write_transport.py``. Every other source file stays transport-free; the
read-only adapter is GET-only and the write adapter refuses non-mock gateways.
The offline reference script may import ``subprocess`` only to invoke the
benchmark repository's CLI; it opens no device, DDS or network path.
"""

from __future__ import annotations

import ast
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

TRANSPORT = Path("src/jcheng_applications/transport.py")
WRITE_TRANSPORT = Path("src/jcheng_applications/write_transport.py")
REFERENCE = Path("scripts/armpi_ore_sorting_reference.py")
TOKEN_ALLOWLIST = {
    TRANSPORT: {"http.client"},
    WRITE_TRANSPORT: {"http.client"},
    # The offline reference invokes the benchmark repository's CLI as a
    # subprocess. It opens no device, DDS or network path and imports no other
    # process-spawning token.
    REFERENCE: {"subprocess"},
}

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
        allowed = TOKEN_ALLOWLIST.get(path.relative_to(ROOT), set())
        for token in FORBIDDEN:
            if token in text and token not in allowed:
                problems.append(f"{path.relative_to(ROOT)}: {token}")
    assert not problems, "forbidden hardware/DDS/network tokens found:\n" + "\n".join(
        problems
    )


def test_transport_is_get_only_and_has_no_motion_call() -> None:
    tree = ast.parse((ROOT / TRANSPORT).read_text(encoding="utf-8"))
    strings = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not ({"POST", "PUT", "PATCH", "DELETE"} & strings)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not ({"submit", "stop", "execute", "arm", "reset"} & called)
    requests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "request"
    ]
    assert requests, "transport must issue its GET through http.client"
    for call in requests:
        assert call.args and isinstance(call.args[0], ast.Constant)
        assert call.args[0].value == "GET"


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

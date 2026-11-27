"""M4 target gate: hardware requires an explicit target, L5 and hardware capability."""

from __future__ import annotations

import json
from pathlib import Path

from jcheng_applications.cli import main

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "recipes" / "example-supervised-transfer.yaml"


def _auth(tmp_path: Path) -> Path:
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "operator": "operator",
                "session_id": "m4-session",
                "scopes": ["arm", "gripper"],
                "approved": True,
                "approved_at": NOW,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run(tmp_path: Path, server: MockGatewayServer, *extra: str) -> int:
    return main(
        [
            "run",
            str(RECIPE),
            "--authorization",
            str(_auth(tmp_path)),
            "--run-id",
            "m4-target",
            "--url",
            f"http://127.0.0.1:{server.port}",
            "--store-dir",
            str(tmp_path / "runs"),
            *extra,
        ]
    )


def test_hardware_target_requires_l5_before_any_gateway_call(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("JCHENG_GATEWAY_TOKEN", TOKEN)
    with MockGatewayServer(capabilities=["hardware"]) as server:
        code = _run(tmp_path, server, "--target", "hardware", "--evidence-class", "L3")
        assert code == 2
        assert server.probe.paths() == []
    assert "requires --evidence-class L5" in capsys.readouterr().err


def test_l5_requires_the_hardware_target(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("JCHENG_GATEWAY_TOKEN", TOKEN)
    with MockGatewayServer() as server:
        code = _run(tmp_path, server, "--evidence-class", "L5")
        assert code == 2
        assert server.probe.paths() == []
    assert "requires --target hardware" in capsys.readouterr().err


def test_hardware_target_refuses_a_mock_only_gateway(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JCHENG_GATEWAY_TOKEN", TOKEN)
    with MockGatewayServer(capabilities=["mock"]) as server:
        code = _run(tmp_path, server, "--target", "hardware", "--evidence-class", "L5")
        assert code == 2
        assert "/v1/leases" not in server.probe.paths()
        assert "/v1/motion/execute" not in server.probe.paths()


def test_hardware_target_runs_against_a_hardware_gateway(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("JCHENG_GATEWAY_TOKEN", TOKEN)
    with MockGatewayServer(capabilities=["hardware"]) as server:
        server.probe.results.extend([completed(), completed()])
        code = _run(tmp_path, server, "--target", "hardware", "--evidence-class", "L5")
        output = json.loads(capsys.readouterr().out)
        assert code == 0
        assert output["target"] == "hardware"
        assert output["state"] == "SUCCEEDED"
        assert server.probe.paths().count("/v1/motion/execute") == 2
        record = (tmp_path / "runs" / "m4-target.jsonl").read_text(encoding="utf-8")
        assert '"evidence_class":"L5"' in record

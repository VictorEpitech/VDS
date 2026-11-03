from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vds_pi_helper import Policy  # noqa: E402

ORIGIN = "https://vds.example.fr"
JSON = "application/json"


def make(reboot: bool = False) -> tuple[Policy, list[list[str]], list[float]]:
    calls: list[list[str]] = []
    t = [1000.0]
    return Policy(ORIGIN, reboot, calls.append, lambda: t[0]), calls, t


def test_foreign_or_missing_origin_is_refused_without_effect() -> None:
    p, calls, _ = make(True)
    for origin in (None, "https://evil.example", "http://127.0.0.1:47801", ORIGIN + "/"):
        assert p.handle("POST", "/v1/restart/device", origin, JSON)[0] == 403
        assert p.handle("GET", "/v1/caps", origin, None)[0] == 403
    assert calls == []


def test_unconfigured_origin_refuses_everything() -> None:
    p = Policy("", True, lambda _c: None)
    assert p.handle("GET", "/v1/caps", "", None)[0] == 403


def test_caps_and_preflight() -> None:
    p, _, _ = make(False)
    s, h, b = p.handle("GET", "/v1/caps", ORIGIN, None)
    assert s == 200 and b == {"helper": "vds-pi", "restart_app": True, "restart_device": False}
    assert h["Access-Control-Allow-Origin"] == ORIGIN
    assert p.handle("OPTIONS", "/v1/restart/app", ORIGIN, None)[0] == 204
    assert "screenshot" not in str(b)


def test_restart_app_runs_systemd_unit_and_is_rate_limited() -> None:
    p, calls, t = make()
    assert p.handle("POST", "/v1/restart/app", ORIGIN, JSON)[0] == 202
    assert calls == [["systemctl", "restart", "vds-kiosk.service"]]
    assert p.handle("POST", "/v1/restart/app", ORIGIN, JSON)[0] == 429
    t[0] += 31
    assert p.handle("POST", "/v1/restart/app", ORIGIN, JSON)[0] == 202
    assert len(calls) == 2


def test_simple_request_without_json_content_type_refused() -> None:
    p, calls, _ = make()
    assert p.handle("POST", "/v1/restart/app", ORIGIN, "text/plain")[0] == 415
    assert p.handle("POST", "/v1/restart/app", ORIGIN, None)[0] == 415
    assert calls == []


def test_device_restart_only_when_allowed() -> None:
    p, calls, _ = make(False)
    assert p.handle("POST", "/v1/restart/device", ORIGIN, JSON)[0] == 403
    assert calls == []
    p2, calls2, _ = make(True)
    assert p2.handle("POST", "/v1/restart/device", ORIGIN, JSON)[0] == 202
    assert calls2 == [["systemctl", "reboot"]]


def test_unknown_paths_and_scopes() -> None:
    p, calls, _ = make(True)
    for path in ("/v1/restart/../x", "/v1/restart/foo", "/v1/screenshot", "/"):
        assert p.handle("POST", path, ORIGIN, JSON)[0] == 404
    assert calls == []

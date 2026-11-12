"""2.0 feature matrix: fast self-checks (mocked cloud, no live HA)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.imou_life.const import DOMAIN

_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _ROOT / "custom_components/imou_life/manifest.json"


@pytest.mark.parametrize(
    ("marker", "module"),
    [
        ("P1_media", "custom_components.imou_life.media_source"),
        ("P2_insight", "custom_components.imou_life.insight_cloud"),
        ("P2_diag", "custom_components.imou_life.sensor"),
        ("P3_workbench", "custom_components.imou_life.insight_workbench"),
        ("P3_services", "services.yaml"),
    ],
)
def test_v2_module_present(marker: str, module: str) -> None:
    """Each req-013 deliverable is importable or present on disk."""
    if module.endswith(".yaml"):
        path = _ROOT / "custom_components" / "imou_life" / module
        assert path.is_file(), f"{marker}: missing {path}"
        return
    __import__(module)


def test_manifest_platforms_for_v2() -> None:
    """Manifest lists media and stream deps for 2.0."""
    mf = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert "media_source" in mf.get("dependencies", [])
    assert "stream" in mf.get("after_dependencies", [])
    assert mf["domain"] == DOMAIN


def test_zh_cloud_storage_sensor_label() -> None:
    """User-facing 智见云存储 label is wired in zh-Hans."""
    zh = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components/imou_life/translations/zh-Hans.json"
        ).read_text(encoding="utf-8")
    )
    name = zh["entity"]["sensor"]["cloud_storage_status"]["name"]
    assert name == "智见云存储"


def test_login_region_options_intl_and_china_only() -> None:
    """Server region selector exposes International + China only."""
    strings = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components/imou_life/strings.json"
        ).read_text(encoding="utf-8")
    )
    opts = strings["selector"]["api_url"]["options"]
    assert set(opts) == {"intl", "cn"}

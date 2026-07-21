# Webhook 报警分类与推送可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 #66：将 `openCamera`/`closeCamera` 等非安防推送排除出 `imou_life_alarm`，并用 runtime 计数 + diagnostics 支撑动检/人形漏推排查。

**Architecture:** 混合判定（默认报警 − `NON_ALARM_MSG_TYPES` 黑名单）；webhook 在通过过滤后更新 `ImouRuntimeData` 内存计数；diagnostics 输出本地 push 配置与近期 `msgType` 统计。不轮询、不调用 `getMessageCallback`。

**Tech Stack:** Home Assistant custom integration (`imou_life`), pytest, pytest-homeassistant-custom-component, ruff, uv

**Spec:** `docs/superpowers/specs/2026-07-21-imou-webhook-alarm-classification-design.md`

## Global Constraints

- 仓库：`Imou-Home-Assistant` only（不改 pyimouapi / HA core）
- 事件名与 payload 字段不变：`imou_life_event` / `imou_life_alarm`
- Webhook 始终 HTTP 200
- 计数仅内存、不持久化、不做实体
- 每个 Task 结束后：相关 pytest 通过；Task 末 `git commit`（勿提交无关 sensor/1.3.1 脏文件）
- 验证命令：`uv run pytest tests/test_webhook.py tests/test_diagnostics.py -q --timeout=30`；最终 `script/lint-check` + `script/test`

## 文件影响总览

| 文件 | 职责 |
|------|------|
| `custom_components/imou_life/webhook.py` | `NON_ALARM_MSG_TYPES`、`_is_alarm_msg_type`、handler 判定与计数调用 |
| `custom_components/imou_life/runtime_data.py` | push 计数字段 + `record_push_msg` |
| `custom_components/imou_life/diagnostics.py` | `event_push` 诊断块 |
| `tests/test_webhook.py` | 分类、notify、计数测试 |
| `tests/test_diagnostics.py` | diagnostics 字段测试 |
| `README.md` | Troubleshooting Event push |
| `CHANGELOG.md` | Fixed 条目（写入当前未发布版本节，现为 `[1.3.1]`） |

---

### Task 1: 报警分类辅助函数（TDD）

**Files:**
- Modify: `custom_components/imou_life/webhook.py`
- Test: `tests/test_webhook.py`

**Interfaces:**
- Produces: `_is_alarm_msg_type(msg_type: str | None) -> bool`
- Produces: `NON_ALARM_MSG_TYPES: frozenset[str]`（可保持模块级私有名 `_NON_ALARM_MSG_TYPES`，但测试从 `webhook` 导入 `_is_alarm_msg_type`）

- [ ] **Step 1: Write the failing tests**

在 `tests/test_webhook.py` 追加：

```python
from custom_components.imou_life.webhook import _is_alarm_msg_type


@pytest.mark.parametrize(
    ("msg_type", "expected"),
    [
        ("closeCamera", False),
        ("openCamera", False),
        ("online", False),
        ("iotProperty", False),
        ("whiteLightOn", False),
        ("bindDevice", False),
        ("videoMotion", True),
        ("human", True),
        ("abAlarmSound", True),
        ("mobileDetect", True),
        ("alarmLocal", True),
        ("totallyUnknownType", True),
        (None, False),
    ],
)
def test_is_alarm_msg_type(msg_type: str | None, expected: bool) -> None:
    """Hybrid classification: denylist non-alarms; unknown types are alarms."""
    assert _is_alarm_msg_type(msg_type) is expected
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/open/projects/Imou-Home-Assistant
uv run pytest tests/test_webhook.py::test_is_alarm_msg_type -v --timeout=30
```

Expected: `ImportError` 或 `AttributeError`（`_is_alarm_msg_type` 不存在）

- [ ] **Step 3: Implement classification in `webhook.py`**

替换原 `_NON_ALARM_TYPES`（约 L23–35）为：

```python
# Status / ops types that must NOT fire imou_life_alarm or notify.
# Official Imou "Device alarm" list also includes privacy-mask and lifecycle
# types; we subtract those here. See:
# https://open.imoulife.com/book/en/push/alarm.html
_NON_ALARM_MSG_TYPES = frozenset(
    {
        # deviceStatus
        "online",
        "offline",
        "close",
        "changeDevName",
        # iot / stats
        "iotEvent",
        "iotProperty",
        "iotAction",
        "numberstat",
        # privacy mask (#66)
        "openCamera",
        "closeCamera",
        # light / siren state
        "whiteLightOn",
        "whiteLightOff",
        "sirenOn",
        "sirenOff",
        # sleep
        "sleep",
        # bind / share / auth / transfer
        "bindDevice",
        "unbindDevice",
        "deviceShare",
        "deviceShareCancel",
        "deviceAuthorize",
        "deviceAuthorizationChanged",
        "transferDeviceFrom",
        "transferDeviceTo",
        "deviceDeletedSharedCancel",
        # upgrade / storage ops
        "UpgradeSuccess",
        "upgradeFail",
        "apUpgradeSuccess",
        "apUpgradeFail",
        "storageRecoverOk",
        "storageRecoverFail",
        "storageEmpty",
        "storageAbnormal",
    }
)


def _is_alarm_msg_type(msg_type: str | None) -> bool:
    """Return True if this push should fire imou_life_alarm / notify."""
    return msg_type is not None and msg_type not in _NON_ALARM_MSG_TYPES
```

将 handler 中：

```python
is_alarm = msg_type not in _NON_ALARM_TYPES
```

改为：

```python
is_alarm = _is_alarm_msg_type(msg_type)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_webhook.py::test_is_alarm_msg_type tests/test_webhook.py::test_webhook_iot_property_is_not_alarm -v --timeout=30
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/imou_life/webhook.py tests/test_webhook.py
git commit -m "$(cat <<'EOF'
fix: classify privacy-mask pushes as non-alarm events

Exclude openCamera/closeCamera and other status/ops msgTypes from
imou_life_alarm so automations and notify are not misfired (#66).

EOF
)"
```

---

### Task 2: Webhook 集成行为（closeCamera / 真报警 / notify）

**Files:**
- Modify: `tests/test_webhook.py`
- Modify: `custom_components/imou_life/webhook.py`（仅当 Task 1 未改全 handler 时）

**Interfaces:**
- Consumes: `_is_alarm_msg_type`, `async_handle_imou_webhook`, `setup_imou_runtime`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_webhook.py` 追加：

```python
@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("msg_type", ["closeCamera", "openCamera"])
async def test_webhook_privacy_mask_is_not_alarm(
    hass: HomeAssistant, msg_type: str
) -> None:
    """Privacy mask open/close fires generic event only."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["device_1"],
        notify_services=["notify.test"],
    )
    hass.services.async_register("notify", "test", lambda call: None)

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": msg_type,
                "did": "device_1",
                "cid": 0,
                "dname": "Cam",
                "time": 1783528060,
            }
        ),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert generic_events[0].data["msg_type"] == msg_type
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("msg_type", ["videoMotion", "human", "abAlarmSound"])
async def test_webhook_security_alarms_fire_alarm_event(
    hass: HomeAssistant, msg_type: str
) -> None:
    """Security alarm msgTypes fire both generic and alarm events."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass, push_enabled=True, selected_devices=["device_1"]
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": msg_type, "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert len(alarm_events) == 1
    assert alarm_events[0].data["msg_type"] == msg_type


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_missing_msg_type_is_not_alarm(
    hass: HomeAssistant,
) -> None:
    """Payload without msgType still fires generic event, not alarm."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass, push_enabled=True, selected_devices=["device_1"]
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []
```

若 `hass.services.async_register("notify", "test", ...)` 在当前 HA 测试环境签名不兼容，改为只断言 `alarm_events == []`（notify 仅在 `is_alarm` 时调用，无 alarm 即无 notify）。优先保留无 alarm 断言即可满足 #66。

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_webhook.py::test_webhook_privacy_mask_is_not_alarm \
  tests/test_webhook.py::test_webhook_security_alarms_fire_alarm_event \
  tests/test_webhook.py::test_webhook_missing_msg_type_is_not_alarm -v --timeout=30
```

Expected: PASS（Task 1 已改 handler）；若 FAIL，检查 handler 是否仍用旧 `_NON_ALARM_TYPES` 名称。

- [ ] **Step 3: Commit**

```bash
git add tests/test_webhook.py
git commit -m "$(cat <<'EOF'
test: cover webhook alarm vs privacy-mask event split

EOF
)"
```

---

### Task 3: Runtime 推送计数

**Files:**
- Modify: `custom_components/imou_life/runtime_data.py`
- Modify: `custom_components/imou_life/webhook.py`
- Test: `tests/test_webhook.py`

**Interfaces:**
- Produces on `ImouRuntimeData`:
  - `push_msg_type_counts: dict[str, int]`
  - `push_last_msg_type: str | None`
  - `push_last_received_at: datetime | None`
  - `record_push_msg(self, msg_type: str | None) -> None`
- Consumes in webhook: after device filter passes, before/after `async_fire(EVENT_IMOU_EVENT)` 调用 `runtime.record_push_msg(msg_type)`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_records_msg_type_counts(hass: HomeAssistant) -> None:
    """Accepted pushes increment runtime msgType counters."""
    runtime = setup_imou_runtime(
        hass, push_enabled=True, selected_devices=["device_1"]
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "closeCamera", "deviceId": "device_1"}),
    )
    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "closeCamera", "deviceId": "device_1"}),
    )
    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "abAlarmSound", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert runtime.push_msg_type_counts["closeCamera"] == 2
    assert runtime.push_msg_type_counts["abAlarmSound"] == 1
    assert runtime.push_last_msg_type == "abAlarmSound"
    assert runtime.push_last_received_at is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_filtered_device_does_not_count(
    hass: HomeAssistant,
) -> None:
    """Unselected devices do not update push counters."""
    runtime = setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["selected_device"],
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "other_device"}),
    )
    await hass.async_block_till_done()

    assert runtime.push_msg_type_counts == {}
    assert runtime.push_last_msg_type is None
```

另加纯单元测试（可不依赖 HA fixture）：

```python
from datetime import UTC, datetime

from custom_components.imou_life.runtime_data import ImouRuntimeData


def test_record_push_msg_caps_distinct_keys() -> None:
    """More than 50 distinct msgTypes fold into _other."""
    runtime = ImouRuntimeData(coordinator=MagicMock())
    for i in range(50):
        runtime.record_push_msg(f"type_{i}")
    assert len(runtime.push_msg_type_counts) == 50
    runtime.record_push_msg("type_extra")
    assert runtime.push_msg_type_counts["_other"] == 1
    assert "type_extra" not in runtime.push_msg_type_counts
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_webhook.py::test_webhook_records_msg_type_counts \
  tests/test_webhook.py::test_webhook_filtered_device_does_not_count \
  tests/test_webhook.py::test_record_push_msg_caps_distinct_keys -v --timeout=30
```

Expected: FAIL（缺字段 / `record_push_msg`）

- [ ] **Step 3: Implement `runtime_data.py`**

```python
"""Runtime data stored on Imou config entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import ImouDataUpdateCoordinator

_MAX_PUSH_MSG_TYPE_KEYS = 50


@dataclass
class ImouRuntimeData:
    """Data attached to a config entry at runtime."""

    coordinator: ImouDataUpdateCoordinator
    push_enabled: bool = False
    selected_devices: list[str] = field(default_factory=list)
    notify_services: list[str] = field(default_factory=list)
    push_msg_type_counts: dict[str, int] = field(default_factory=dict)
    push_last_msg_type: str | None = None
    push_last_received_at: datetime | None = None

    def record_push_msg(self, msg_type: str | None) -> None:
        """Record an accepted push for diagnostics (in-memory only)."""
        key = msg_type if msg_type is not None else "_unknown"
        if (
            key not in self.push_msg_type_counts
            and len(self.push_msg_type_counts) >= _MAX_PUSH_MSG_TYPE_KEYS
        ):
            key = "_other"
        self.push_msg_type_counts[key] = self.push_msg_type_counts.get(key, 0) + 1
        self.push_last_msg_type = msg_type if msg_type is not None else "_unknown"
        self.push_last_received_at = datetime.now(UTC)
```

注意：封顶后 `push_last_msg_type` 仍应反映真实类型还是 `_other`？按可观测性意图，**last 用真实 `msg_type`（或 `_unknown`）**，计数键在封顶时用 `_other`：

```python
    def record_push_msg(self, msg_type: str | None) -> None:
        """Record an accepted push for diagnostics (in-memory only)."""
        display = msg_type if msg_type is not None else "_unknown"
        count_key = display
        if (
            count_key not in self.push_msg_type_counts
            and len(self.push_msg_type_counts) >= _MAX_PUSH_MSG_TYPE_KEYS
        ):
            count_key = "_other"
        self.push_msg_type_counts[count_key] = (
            self.push_msg_type_counts.get(count_key, 0) + 1
        )
        self.push_last_msg_type = display
        self.push_last_received_at = datetime.now(UTC)
```

同步调整 `test_record_push_msg_caps_distinct_keys`：`push_last_msg_type == "type_extra"` 且 `counts["_other"] == 1`。

- [ ] **Step 4: Wire webhook**

在 `async_handle_imou_webhook` 中，设备过滤通过之后、`hass.bus.async_fire(EVENT_IMOU_EVENT, event_data)` 之前插入：

```python
    runtime.record_push_msg(msg_type)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_webhook.py -q --timeout=30
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/imou_life/runtime_data.py \
  custom_components/imou_life/webhook.py tests/test_webhook.py
git commit -m "$(cat <<'EOF'
feat: track webhook msgType counts on runtime data

Expose in-memory counters for diagnostics so missing motion/human
pushes can be distinguished from misclassification (#66).

EOF
)"
```

---

### Task 4: Diagnostics `event_push` 块

**Files:**
- Modify: `custom_components/imou_life/diagnostics.py`
- Modify: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `ImouRuntimeData.push_*`、`PARAM_EVENT_PUSH_TYPES`、`PARAM_BASE_PUSH`、`DEFAULT_BASE_PUSH`
- Produces diagnostics key `event_push: dict[str, Any]`

- [ ] **Step 1: Write the failing test**

更新/扩展 `tests/test_diagnostics.py`：

```python
"""Tests for Imou Life diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_BASE_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.diagnostics import async_get_config_entry_diagnostics
from custom_components.imou_life.runtime_data import ImouRuntimeData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_redacts_secrets(hass) -> None:
    """Diagnostics must not expose App Secret."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "abcd1234efgh5678"},
        options={"enable_event_push": True},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.last_update_success = True
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "app_secret" not in result
    assert result["app_id"] == "test…"
    assert result["webhook_id"] == "abcd1234…"
    assert result["event_push_enabled"] is True
    assert result["last_update_success"] is True
    assert "pyimouapi_version" in result
    assert result["event_push"]["enabled"] is True
    assert result["event_push"]["recent_msg_type_counts"] == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_includes_push_msg_counts(hass) -> None:
    """Diagnostics expose runtime push msgType counters."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "abcd1234efgh5678"},
        options={
            "enable_event_push": True,
            PARAM_EVENT_PUSH_TYPES: ["alarm", "device_status"],
            PARAM_BASE_PUSH: "2",
            "webhook_url": "https://example.com/hook",
        },
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.record_push_msg("closeCamera")
    runtime.record_push_msg("abAlarmSound")
    entry.runtime_data = runtime

    result = await async_get_config_entry_diagnostics(hass, entry)
    event_push = result["event_push"]

    assert event_push["enabled"] is True
    assert event_push["webhook_url_configured"] is True
    assert event_push["event_push_types"] == ["alarm", "device_status"]
    assert event_push["base_push"] == "2"
    assert event_push["recent_msg_type_counts"] == {
        "closeCamera": 1,
        "abAlarmSound": 1,
    }
    assert event_push["last_msg_type"] == "abAlarmSound"
    assert event_push["last_received_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_diagnostics.py -v --timeout=30
```

Expected: FAIL（缺 `event_push`）

- [ ] **Step 3: Implement diagnostics**

```python
"""Diagnostics support for Imou Life."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_BASE_PUSH,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_BASE_PUSH,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    app_id = entry.data.get(PARAM_APP_ID, "")
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    webhook_url = entry.options.get(PARAM_WEBHOOK_URL, "")
    selected = entry.options.get(PARAM_SELECTED_DEVICES) or entry.data.get(
        PARAM_SELECTED_DEVICES, []
    )

    runtime = entry.runtime_data
    coordinator = runtime.coordinator if runtime is not None else None
    last_update_success = (
        coordinator.last_update_success if coordinator is not None else None
    )

    last_received = (
        runtime.push_last_received_at.isoformat()
        if runtime is not None and runtime.push_last_received_at is not None
        else None
    )

    event_push = {
        "enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_url_configured": bool(webhook_url),
        "event_push_types": list(entry.options.get(PARAM_EVENT_PUSH_TYPES, [])),
        "base_push": entry.options.get(PARAM_BASE_PUSH, DEFAULT_BASE_PUSH),
        "selected_devices_count": len(selected),
        "recent_msg_type_counts": (
            dict(runtime.push_msg_type_counts) if runtime is not None else {}
        ),
        "last_msg_type": (
            runtime.push_last_msg_type if runtime is not None else None
        ),
        "last_received_at": last_received,
    }

    return {
        "app_id": f"{app_id[:4]}…" if len(app_id) > 4 else app_id,
        "api_url": entry.data.get(PARAM_API_URL),
        "selected_devices_count": len(selected),
        "event_push_enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_id": f"{webhook_id[:8]}…" if len(webhook_id) > 8 else webhook_id,
        "webhook_url_configured": bool(webhook_url),
        "last_update_success": last_update_success,
        "pyimouapi_version": version("pyimouapi"),
        "event_push": event_push,
    }
```

保留顶层旧字段（`event_push_enabled` 等）以免破坏已有诊断阅读习惯；新细节放在 `event_push` 下。

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_diagnostics.py tests/test_webhook.py -q --timeout=30
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/imou_life/diagnostics.py tests/test_diagnostics.py
git commit -m "$(cat <<'EOF'
feat: expose webhook push msgType stats in diagnostics

EOF
)"
```

---

### Task 5: README + CHANGELOG + 全量验证

**Files:**
- Modify: `README.md`（Troubleshooting 节）
- Modify: `CHANGELOG.md`（`[1.3.1]` Fixed）

- [ ] **Step 1: Update README Troubleshooting**

将现有 Event push 条目扩展为（保留原有 URL/repair 句，并追加）：

```markdown
- **Event push not working** — Check **Settings → System → Network → Home Assistant URL** (external URL required), or set a manual callback URL in **Configure**. Review repair issues under **Settings → System → Repairs**.
  - Automations can listen to `imou_life_event` (all accepted pushes) and `imou_life_alarm` (security alarms only). Privacy-mask messages (`openCamera` / `closeCamera`) fire only `imou_life_event`.
  - If you receive `abAlarmSound` or `closeCamera`, the callback/webhook path is working.
  - If `videoMotion` / `human` / `mobileDetect` never appear: confirm motion/human detection is enabled on the device; download **Diagnostics** and check `event_push.recent_msg_type_counts`. Missing keys mean the cloud/device did not push those types (not an HA misclassification).
  - Confirm event push is enabled in **Configure** and push types include **alarm**.
```

- [ ] **Step 2: Update CHANGELOG under `[1.3.1]`**

在 `### Changed` 旁增加或合并：

```markdown
### Fixed
- Webhook: treat privacy-mask and other status/ops msgTypes (`openCamera`, `closeCamera`, …) as non-alarm (`imou_life_event` only); expose recent push msgType counts in diagnostics (#66)
```

若 `[1.3.1]` 尚无 `### Fixed`，新建该小节。勿改动无关 sensor changelog 措辞。

- [ ] **Step 3: Full verification**

```bash
script/lint-check
script/test
```

Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs: document webhook alarm classification and push diagnostics

EOF
)"
```

---

## Spec coverage checklist（自检）

| Spec 要求 | Task |
|-----------|------|
| 混合分类 / `openCamera`/`closeCamera` 非报警 | Task 1–2 |
| 真报警仍发 `imou_life_alarm` | Task 2 |
| `msg_type is None` 不报警 | Task 1–2 |
| Runtime 计数 + 50 键上限 | Task 3 |
| 过滤/关闭 push 不计数 | Task 3 |
| Diagnostics `event_push` | Task 4 |
| README 排查 | Task 5 |
| 不轮询 / 不 getMessageCallback | 全计划未引入 |
| 事件名/payload 不变 | 全计划未改 normalize 字段 |

## Placeholder scan

无 TBD / “类似 Task N” / 空测试步骤。

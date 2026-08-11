# Imou Select PR #177456 Review Response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 maintainer review 收窄并修正 core `imou` select PR：去掉 `mode`、设置类实体标 CONFIG、对齐 `_iter_selects`、可翻译异常、清理 icons/测试，并准备英文回复。

**Architecture:** 在已有 `imou-select` 分支上增量修改；select 仅保留 volume + night vision（均为 `EntityCategory.CONFIG`）；setup 与 switch/sensor 同构的 `_iter_selects`；`ImouException` → `HomeAssistantError(translation_domain=DOMAIN, translation_key="select_option_failed")`。

**Tech Stack:** Home Assistant Core (`imou`)，`pyimouapi`（本 PR 不 bump），pytest / syrupy / freezegun，ruff，`script.translations develop`

**Spec:** `docs/superpowers/specs/2026-08-11-imou-select-pr-177456-review-response-design.md`

## Global Constraints

- 工作目录：`/home/open/projects/core`，分支：`imou-select`（跟踪 `origin/imou-select`）；先 `git checkout imou-select && git pull`
- **不**实现 `alarm_control_panel`；**不**改 `conftest.init_integration` 返回类型；**不**大范围把 `PARAM_STATE`/`PARAM_STATUS` 改从库 import；**不**给 button/switch/camera 加异常翻译
- 遵守 `.cursor/rules/ha-core-integration-pr.mdc`（含 #177456 增补项）
- `strings.json` 用 sentence case；PR 模板 checklist 不得删项
- 每个 Task 末在 **core** 仓库提交（英文 commit message）

## 文件影响总览

| 文件 | 动作 | 职责 |
|------|------|------|
| `homeassistant/components/imou/select.py` | Modify | 去 mode；CONFIG；`_iter_selects`；可翻译异常 |
| `homeassistant/components/imou/strings.json` | Modify | 删 mode；加 `exceptions.select_option_failed` |
| `homeassistant/components/imou/icons.json` | Modify | 删 mode；删 volume `high` 重复 default |
| `homeassistant/components/imou/const.py` | Modify | 删除仅被 select 使用的 `PARAM_MODE` |
| `tests/components/imou/const.py` | Modify | `DEFAULT_SELECTS` 去掉 mode |
| `tests/components/imou/test_select.py` | Modify | 改测 volume/night vision；fixture 用法；内联；异常 match |
| `tests/components/imou/snapshots/test_select.ambr` | Modify | 重生 snapshot |

---

### Task 1: 切换分支并收窄测试夹具（去掉 mode）

**Files:**
- Modify: `tests/components/imou/const.py`（`DEFAULT_SELECTS`）
- Modify: `homeassistant/components/imou/const.py`（删除 `PARAM_MODE`）

**Interfaces:**
- Consumes: 现有 `PARAM_NIGHT_VISION_MODE` / `PARAM_DEVICE_VOLUME`
- Produces: `DEFAULT_SELECTS` 仅含 night vision + volume；集成 `const` 不再导出 `PARAM_MODE`

- [ ] **Step 1: Checkout `imou-select`**

```bash
cd /home/open/projects/core
git checkout imou-select
git pull --ff-only origin imou-select
```

Expected: 工作区在最新 `imou-select`。

- [ ] **Step 2: 从测试夹具移除 mode**

在 `tests/components/imou/const.py`：

1. 从 `homeassistant.components.imou.const` 的 import 中删除 `PARAM_MODE`。
2. 将 `DEFAULT_SELECTS` 改为：

```python
DEFAULT_SELECTS = {
    PARAM_NIGHT_VISION_MODE: {
        PARAM_CURRENT_OPTION: "intelligent",
        PARAM_OPTIONS: ["intelligent", "fullcolor", "infrared", "off"],
    },
    PARAM_DEVICE_VOLUME: {
        PARAM_CURRENT_OPTION: "medium",
        PARAM_OPTIONS: ["mute", "low", "medium", "high"],
    },
}
```

- [ ] **Step 3: 从集成 const 删除 `PARAM_MODE`**

在 `homeassistant/components/imou/const.py` 删除：

```python
PARAM_MODE = "mode"
```

- [ ] **Step 4: 确认测试因生产代码仍引用 `PARAM_MODE` 而失败**

```bash
cd /home/open/projects/core
.venv/bin/pytest tests/components/imou/test_select.py -q
```

Expected: FAIL（`select.py` 仍 `from .const import ... PARAM_MODE` → ImportError，或实体/断言与夹具不一致）。

- [ ] **Step 5: Commit**

```bash
git add tests/components/imou/const.py homeassistant/components/imou/const.py
git commit -m "$(cat <<'EOF'
test: drop mode from Imou select fixtures

Mode will move to a follow-up alarm_control_panel platform.
EOF
)"
```

---

### Task 2: 重写 `test_select.py`（TDD：先改测试）

**Files:**
- Modify: `tests/components/imou/test_select.py`

**Interfaces:**
- Consumes: `mock_imou_ha_device_manager`；`PARAM_NIGHT_VISION_MODE` / `PARAM_DEVICE_VOLUME`
- Produces: 测试期望 unique_id `d1$night_vision_mode` / `d1$device_volume`；错误 match `Error communicating with the Imou API`；不再引用 `PARAM_MODE` / 模块级 `_apply_select_option` / `init_integration` 返回值做 side_effect

- [ ] **Step 1: 用以下完整内容覆盖 `tests/components/imou/test_select.py`**

```python
"""Tests for Imou select platform."""

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.imou.const import (
    PARAM_DEVICE_VOLUME,
    PARAM_NIGHT_VISION_MODE,
    PARAM_STATE,
    PARAM_STATUS,
)
from homeassistant.components.imou.coordinator import SCAN_INTERVAL
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import UNKNOWN_SELECT_KEY, create_online_device, select_mock_devices

from tests.common import MockConfigEntry, async_fire_time_changed, snapshot_platform


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize("imou_mock_devices", [select_mock_devices], indirect=True)
@pytest.mark.usefixtures("init_integration")
async def test_select_entities_snapshot(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Snapshot select entities created from the mock device list."""
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize(
    "imou_mock_devices",
    [
        [
            create_online_device(
                "d1",
                "Device 1",
                button_keys=(),
                selects={
                    UNKNOWN_SELECT_KEY: {
                        PARAM_CURRENT_OPTION: "0",
                        PARAM_OPTIONS: ["0", "1"],
                    },
                    PARAM_NIGHT_VISION_MODE: {
                        PARAM_CURRENT_OPTION: "intelligent",
                        PARAM_OPTIONS: ["intelligent", "fullcolor", "infrared", "off"],
                    },
                },
            )
        ]
    ],
    indirect=True,
)
@pytest.mark.usefixtures("init_integration")
async def test_setup_ignores_unknown_select_types(
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unknown select keys from the API are not turned into entities."""
    entries = er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )
    select_entries = [entry for entry in entries if entry.domain == SELECT_DOMAIN]
    assert len(select_entries) == 1
    assert select_entries[0].translation_key == PARAM_NIGHT_VISION_MODE


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize("imou_mock_devices", [select_mock_devices], indirect=True)
@pytest.mark.usefixtures("init_integration")
async def test_select_option_via_domain_service(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_imou_ha_device_manager: MagicMock,
) -> None:
    """Selecting an option calls the vendor library through the coordinator."""

    async def _side_effect(
        device: ImouHaDevice, select_type: str, option: str
    ) -> None:
        device.selects[select_type][PARAM_CURRENT_OPTION] = option

    mock_imou_ha_device_manager.async_select_option.side_effect = _side_effect
    volume_entry = next(
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == "d1$device_volume"
    )

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: volume_entry.entity_id, ATTR_OPTION: "high"},
        blocking=True,
    )

    mock_imou_ha_device_manager.async_select_option.assert_awaited_once()
    call = mock_imou_ha_device_manager.async_select_option.await_args
    assert call is not None
    assert call.args[1] == PARAM_DEVICE_VOLUME
    assert call.args[2] == "high"
    assert hass.states.get(volume_entry.entity_id).state == "high"


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize("imou_mock_devices", [select_mock_devices], indirect=True)
@pytest.mark.usefixtures("init_integration")
async def test_select_option_propagates_api_error(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_imou_ha_device_manager: MagicMock,
) -> None:
    """Imou API errors from async_select_option surface to the service call."""
    mock_imou_ha_device_manager.async_select_option.side_effect = ImouException(
        "cloud failure"
    )

    volume_entry = next(
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == "d1$device_volume"
    )

    with pytest.raises(
        HomeAssistantError, match="Error communicating with the Imou API"
    ):
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: volume_entry.entity_id, ATTR_OPTION: "high"},
            blocking=True,
        )


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize(
    "imou_mock_devices",
    [
        [
            create_online_device(
                "d1",
                "Device 1",
                button_keys=(),
                selects={
                    PARAM_NIGHT_VISION_MODE: {
                        PARAM_CURRENT_OPTION: "intelligent",
                        PARAM_OPTIONS: ["intelligent", "fullcolor", "infrared", "off"],
                    }
                },
            )
        ]
    ],
    indirect=True,
)
@pytest.mark.usefixtures("init_integration")
async def test_select_option_unavailable_offline_device(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_imou_ha_device_manager: MagicMock,
) -> None:
    """Selecting an option on an offline device does not call the vendor library."""
    night_entry = next(
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == "d1$night_vision_mode"
    )

    async def set_device_offline(device: ImouHaDevice) -> None:
        device._sensors[PARAM_STATUS] = {PARAM_STATE: DeviceStatus.OFFLINE.value}

    mock_imou_ha_device_manager.async_update_device_status.side_effect = (
        set_device_offline
    )
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(night_entry.entity_id).state == STATE_UNAVAILABLE

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: night_entry.entity_id, ATTR_OPTION: "fullcolor"},
        blocking=True,
    )

    mock_imou_ha_device_manager.async_select_option.assert_not_called()


@pytest.mark.parametrize("platforms", [[Platform.SELECT]], indirect=True)
@pytest.mark.parametrize("imou_mock_devices", [select_mock_devices], indirect=True)
@pytest.mark.usefixtures("init_integration")
async def test_entities_removed_when_device_leaves_account(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_imou_ha_device_manager: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Select entities are removed when the device is no longer on the account."""
    volume_entry = next(
        entry
        for entry in er.async_entries_for_config_entry(
            entity_registry, mock_config_entry.entry_id
        )
        if entry.unique_id == "d1$device_volume"
    )
    assert hass.states.get(volume_entry.entity_id).state != STATE_UNAVAILABLE

    mock_imou_ha_device_manager.async_get_devices.return_value = []

    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert (
        er.async_entries_for_config_entry(entity_registry, mock_config_entry.entry_id)
        == []
    )
    assert hass.states.get(volume_entry.entity_id) is None
```

约定：

- Setup 一律 `@pytest.mark.usefixtures("init_integration")`，不要注入未使用的 `init_integration` 参数。
- side_effect / assert **只用** `mock_imou_ha_device_manager`。
- `_side_effect` 仅存在于 `test_select_option_via_domain_service` 内部（满足单次使用内联）。

- [ ] **Step 2: 跑测试确认失败原因来自生产代码未改**

```bash
.venv/bin/pytest tests/components/imou/test_select.py -q
```

Expected: FAIL（`select.py` 仍 import `PARAM_MODE`，和/或错误文案仍为 `cloud failure`，和/或仍创建 mode 实体）。

- [ ] **Step 3: Commit**

```bash
git add tests/components/imou/test_select.py
git commit -m "$(cat <<'EOF'
test: retarget Imou select tests off mode entity

Use mock_imou_ha_device_manager for side effects and assert
translated API errors for the upcoming select exception strings.
EOF
)"
```

---

### Task 3: 实现 `select.py`（去 mode、CONFIG、`_iter_selects`、可翻译异常）

**Files:**
- Modify: `homeassistant/components/imou/select.py`

**Interfaces:**
- Consumes: `DOMAIN` from `.const`；`EntityCategory` from `homeassistant.const`；`ImouDataUpdateCoordinator` from `.coordinator`
- Produces: `_iter_selects(coordinator) -> list[tuple[SelectEntityDescription, ImouHaDevice]]`；`SELECT_TYPES` 仅 volume + night vision（均 CONFIG）；`async_select_option` 抛 `translation_key="select_option_failed"`

- [ ] **Step 1: 用以下完整内容替换 `homeassistant/components/imou/select.py`**

```python
"""Support for Imou select entities."""

from typing import override

from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    DOMAIN,
    PARAM_DEVICE_VOLUME,
    PARAM_NIGHT_VISION_MODE,
    imou_device_identifier,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity

PARALLEL_UPDATES = 0

SELECT_TYPES: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key=PARAM_DEVICE_VOLUME,
        entity_category=EntityCategory.CONFIG,
        translation_key=PARAM_DEVICE_VOLUME,
    ),
    SelectEntityDescription(
        key=PARAM_NIGHT_VISION_MODE,
        entity_category=EntityCategory.CONFIG,
        translation_key=PARAM_NIGHT_VISION_MODE,
    ),
)


def _iter_selects(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[SelectEntityDescription, ImouHaDevice]]:
    """Return (description, device) pairs for supported selects."""
    return [
        (description, device)
        for device in coordinator.devices
        for description in SELECT_TYPES
        if description.key in device.selects
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou select entities."""
    coordinator = entry.runtime_data

    def _add_selects(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_device_identifier(device) for device in new_devices}
        async_add_entities(
            ImouSelect(coordinator, description, device)
            for description, device in _iter_selects(coordinator)
            if imou_device_identifier(device) in device_keys
        )

    entry.async_on_unload(coordinator.register_new_device_callback(_add_selects))
    _add_selects(coordinator.devices)


class ImouSelect(ImouEntity, SelectEntity):
    """Imou select entity."""

    entity_description: SelectEntityDescription

    @property
    @override
    def options(self) -> list[str]:
        """Return a list of selectable options."""
        return self.device.selects[self._entity_type][PARAM_OPTIONS]

    @property
    @override
    def current_option(self) -> str | None:
        """Return the current selected option."""
        return self.device.selects[self._entity_type][PARAM_CURRENT_OPTION]

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            await self.coordinator.device_manager.async_select_option(
                self.device,
                self._entity_type,
                option,
            )
        except ImouException as e:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="select_option_failed",
            ) from e
        await self.coordinator.async_request_refresh()
```

- [ ] **Step 2: Commit**

```bash
git add homeassistant/components/imou/select.py
git commit -m "$(cat <<'EOF'
fix: align Imou select with review (no mode, CONFIG, iter)

Drop mode for a follow-up alarm_control_panel, mark settings as
CONFIG, reuse _iter_selects, and raise translated API errors.
EOF
)"
```

---

### Task 4: `strings.json` + `icons.json` + snapshot

**Files:**
- Modify: `homeassistant/components/imou/strings.json`
- Modify: `homeassistant/components/imou/icons.json`
- Modify: `tests/components/imou/snapshots/test_select.ambr`

**Interfaces:**
- Produces: `exceptions.select_option_failed.message` = `Error communicating with the Imou API`；无 `entity.select.mode`；volume icons 无重复 `high`

- [ ] **Step 1: 更新 `strings.json`**

1. 删除 `entity.select` 下整个 `"mode": { ... }` 块。
2. 在顶层 `"config"` 与 `"entity"` 之间加入（保持 key 字母序时可接受）：

```json
  "exceptions": {
    "select_option_failed": {
      "message": "Error communicating with the Imou API"
    }
  },
```

目标结构：

```json
{
  "config": { },
  "exceptions": {
    "select_option_failed": {
      "message": "Error communicating with the Imou API"
    }
  },
  "entity": {
    "select": {
      "device_volume": { },
      "night_vision_mode": { }
    }
  },
  "selector": { }
}
```

- [ ] **Step 2: 更新 `icons.json`**

1. 删除 `entity.select.mode` 整块。
2. `device_volume.state` 删除 `"high": "mdi:volume-high"`，保留：

```json
      "device_volume": {
        "default": "mdi:volume-high",
        "state": {
          "low": "mdi:volume-low",
          "medium": "mdi:volume-medium",
          "mute": "mdi:volume-off"
        }
      },
```

- [ ] **Step 3: develop 翻译并更新 snapshot**

```bash
cd /home/open/projects/core
python3 -m script.translations develop --integration imou
.venv/bin/pytest tests/components/imou/test_select.py -q --snapshot-update
```

Expected: 功能测试 PASS；snapshot 反映 `entity_category=config` 且无 mode。

- [ ] **Step 4: 全量 imou 测试 + ruff**

```bash
.venv/bin/ruff format homeassistant/components/imou/ tests/components/imou/
.venv/bin/ruff check homeassistant/components/imou/ tests/components/imou/
.venv/bin/pytest tests/components/imou/ -q
```

Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add \
  homeassistant/components/imou/strings.json \
  homeassistant/components/imou/icons.json \
  tests/components/imou/snapshots/test_select.ambr
git commit -m "$(cat <<'EOF'
fix: translate Imou select errors and clean select strings/icons

Add exceptions.select_option_failed, remove mode translations/icons,
and drop the redundant volume high state icon.
EOF
)"
```

---

### Task 5: 推送、回复 reviewer、Ready for review

**Files:**
- 无代码（GitHub PR 评论）

- [ ] **Step 1: Push**

```bash
cd /home/open/projects/core
git push -u origin imou-select
```

- [ ] **Step 2: 在 PR #177456 英文回复**

```bash
gh pr comment 177456 --repo home-assistant/core --body "$(cat <<'EOF'
Thanks for the review — changes are in place on this PR:

1. **mode** — Agreed. Removed the mode select entity from this PR. We'll introduce an `alarm_control_panel` platform in a follow-up (gateway arming for subordinate detectors).
2. **`_iter_selects`** — Aligned with `_iter_switches` / `_iter_sensors`.
3. **`EntityCategory.CONFIG`** — Applied to `device_volume` and `night_vision_mode`.
4. **icons** — Removed the redundant `high` state icon (same as default).
5. **Exceptions** — `async_select_option` now raises a translated `HomeAssistantError` (`exceptions.select_option_failed`). We'll follow up to apply the same pattern on button / switch / camera.
6. **Tests** — Side effects/assertions use `mock_imou_ha_device_manager`; inlined the one-shot option helper; dropped the unused `hass` parameter.

Follow-ups (separate PRs), as suggested:
- Import shared device param constants from pyimouapi (e.g. `PARAM_STATE` / `PARAM_STATUS`) instead of redefining them in `.const`
- Change `init_integration` to return `MockConfigEntry` (not the device manager)
- `alarm_control_panel` for mode
EOF
)"
```

- [ ] **Step 3: Ready for review**

```bash
gh pr ready 177456 --repo home-assistant/core
```

- [ ] **Step 4:（可选）同步 docs PR**  
若 `home-assistant.io#47093` 仍描述 Mode select，另开小提交删除 Mode（非本 core plan 强制范围）。

---

## Spec coverage checklist（自检）

| Spec 项 | Task |
|---------|------|
| 移除 mode select | 1, 2, 3, 4 |
| CONFIG on volume / night vision | 3 |
| `_iter_selects` | 3 |
| 可翻译异常 + strings | 3, 4 |
| icons 去 high / mode | 4 |
| 测试用 mock manager；内联 helper；去未用 hass | 2 |
| 不改 init_integration 返回类型 / const 库 import / alarm panel | Global + Task 5 回复 |
| 规则沉淀 | 已在 brainstorm 完成（非本 plan） |
| Reviewer 英文回复 | 5 |

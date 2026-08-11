# Imou Select PR #177456 Review 响应 — 设计规格

**日期** 2026-08-11  
**状态** 已批准（对话确认）  
**范围** 响应 [home-assistant/core#177456](https://github.com/home-assistant/core/pull/177456) 上 `justanotherariel` review（[review 4895358913](https://github.com/home-assistant/core/pull/177456#pullrequestreview-4895358913)）；仅处理 **当前 select PR** 的改动与回复口径。  
**规范** `/home/open/projects/.cursor/rules/ha-core-integration-pr.mdc`（本轮同步增补）  
**前置** `/home/open/projects/docs/superpowers/specs/2026-07-28-imou-select-design.md`

---

## 1. 背景与目标

### 背景

Maintainer `justanotherariel` 对 select PR 提出 Changes requested。核心分歧点是 `mode`（home / away / disarm）：选项语义与布撤防行为接近 `alarm_control_panel`。经确认：切换 mode **会**改变网关下属探测器的布撤防，但**不会**影响摄像头等其它设备类型。按 HA 惯例，作用域限于网关仍适用 `alarm_control_panel`，因此本 PR **接受**拆分建议。

### 目标

1. 在本 PR 内落地所有「应在本 PR 修改」的意见。
2. 用英文回复登记同意项与 follow-up PR。
3. 将本轮教训写入 `ha-core-integration-pr.mdc`，供后续自检。

### 非目标

- 实现 `alarm_control_panel`（另开 PR）。
- 将 `PARAM_STATE` / `PARAM_STATUS` 等从 pyimouapi 统一 import（另开 PR）。
- 修改 `init_integration` 使其返回 `MockConfigEntry`（另开 PR）。
- 为 button / switch / camera 统一异常翻译（另开 PR；本 PR **仅** select）。
- 社区集成 `imou_life` 同步（不在本规格范围）。

### 已确认决策

| 维度 | 决策 |
|------|------|
| `mode` | 从本 PR 移除；另开 `alarm_control_panel` |
| 处理策略 | 方案 1：本 PR 必改 + 回复登记 follow-up |
| 异常翻译 | 本 PR 为 select 落地；参考 core（如 Ohme），**不**抄社区 `HomeAssistantError(err.message)` |
| 范围 | 仅当前 select PR |

---

## 2. Review 意见对照

| # | 意见摘要 | 本 PR 动作 |
|---|----------|------------|
| 1 | `mode` → alarm control panel | **移除** mode select；回复同意另开 PR |
| 2 | 对齐 `_iter_switches` / `_iter_sensors` | 增加 `_iter_selects`，setup 复用 |
| 3 | const 从库 import（STATE/STATUS 等） | **不改代码**；回复另开 PR |
| 4 | `icons.json` high 与 default 重复 | 删除 `"high": "mdi:volume-high"` |
| 5 | volume 非主实体 | `entity_category=EntityCategory.CONFIG` |
| 6 | night vision 同上 | 同上 |
| 7 | 翻译异常 | select 使用 `HomeAssistantError(translation_domain=DOMAIN, translation_key=...)` + `strings.json` → `exceptions` |
| 8 | 测试未用 `hass` | 删除未用参数 |
| 9 | 勿用 `init_integration` 返回值做 side_effect/assert | select 测试改用 `mock_imou_ha_device_manager`；**不**改 conftest 返回类型 |
| 10 | `_apply_select_option` 仅用一次 | 内联到唯一调用处 |

---

## 3. 代码设计

### 3.1 实体范围

`SELECT_TYPES` 仅保留：

- `PARAM_DEVICE_VOLUME` — `entity_category=EntityCategory.CONFIG`
- `PARAM_NIGHT_VISION_MODE` — `entity_category=EntityCategory.CONFIG`

删除 `PARAM_MODE` 及对应 strings / icons / fixtures / snapshots / 测试。

### 3.2 `_iter_selects`

与 `switch.py` / `sensor.py` 同构：

```python
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
```

`_add_selects`：对 `new_devices` 算 `device_keys`，再对 `_iter_selects(coordinator)` 过滤后 `async_add_entities`。

### 3.3 异常翻译

```python
except ImouException as e:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="select_option_failed",
    ) from e
```

`strings.json` 增加（英文 sentence case）：

```json
"exceptions": {
  "select_option_failed": {
    "message": "Error communicating with the Imou API"
  }
}
```

不把原始 `str(e)` 暴露为唯一用户文案（与 Ohme `api_failed` 一致）。测试中断言异常时改为匹配翻译后的消息或 `translation_key` 行为（按现有 HA 测试惯例）。

### 3.4 icons

`device_volume.state` 删除与 `default` 相同的 `"high"` 项。

### 3.5 测试

- side_effect / call assert：注入并使用 `mock_imou_ha_device_manager`，不要 `manager = await init_integration(...)` 再对返回值操作。
- `init_integration` 仍可调用以完成 setup；忽略其返回值，或写成 `await init_integration(...)`。
- 删除仅用一次的 `_apply_select_option`；在唯一测试内直接改 `device.selects[...][PARAM_CURRENT_OPTION]`。
- 去掉未使用的 `hass` 参数。
- 更新 / 重生 snapshot（改 strings / entity category 后跑 `script.translations develop --integration imou` 再 snapshot）。

---

## 4. Reviewer 回复口径（英文要点）

1. **mode** — Agree; removed from this PR. Will introduce `alarm_control_panel` in a follow-up (gateway arming for subordinate detectors).
2. **Done here** — `_iter_selects`; `EntityCategory.CONFIG` for volume and night vision; icons cleanup; select exception translation; test fixture usage; inline one-shot helper.
3. **Follow-ups** — Import device param constants from pyimouapi (`PARAM_STATE` / `PARAM_STATUS`, etc.); change `init_integration` to return `MockConfigEntry` (or not return the manager); translate exceptions consistently on button / switch / camera.

---

## 5. 错误处理与验证

- Ruff format/check on `imou` component + tests.
- `pytest tests/components/imou/`（至少 `test_select.py` + 受影响 snapshot）。
- `script.translations develop --integration imou` 后更新 ambr。
- Ready for review 前确认 PR 模板 checklist 完整。

---

## 6. 规则沉淀

将本轮意见写入 `/home/open/projects/.cursor/rules/ha-core-integration-pr.mdc`（实体平台选择、`EntityCategory.CONFIG`、`_iter_*` 对齐、icons 去重、可翻译异常、测试 fixture 用法、follow-up 拆分）。后续在 core Imou PR 自检时必须按该规则核对。

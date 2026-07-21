# Webhook 报警分类与推送可观测性 — 设计规格

**日期:** 2026-07-21  
**状态:** 已批准  
**范围:** `Imou-Home-Assistant/custom_components/imou_life`  
**关联:** [Imou-Home-Assistant#66](https://github.com/Imou-OpenPlatform/Imou-Home-Assistant/issues/66)  
**决策:** 方案 2 — 混合分类 + 本地 runtime 计数 / diagnostics；不轮询、不调用 `getMessageCallback`

## 摘要

修复 webhook 将 `openCamera` / `closeCamera` 等非安防消息误判为 `imou_life_alarm` 的问题；同时用内存计数与 diagnostics 帮助区分「云端未推送」与「收到但分错类」，便于排查动检 / 人形漏推。不改变事件名与 payload 字段，不引入报警轮询兜底。

## 问题陈述

用户（Ranger 2C Pro，海外区，集成 1.3.0）报告：

1. **误分类：** `closeCamera` / `openCamera` 触发了 `imou_life_alarm`（用户认为这不是报警）。
2. **漏推：** 动检 / 人形未通过 webhook 到达；目前能稳定看到的报警类推送主要是 `abAlarmSound`。

根因（误分类）：`webhook.py` 用黑名单 `_NON_ALARM_TYPES` 判定 `is_alarm = msg_type not in _NON_ALARM_TYPES`，但未包含 `openCamera` / `closeCamera`（官方文档将二者归在 Device alarm 大类，语义为隐私罩开关，不是安防报警）。现有黑名单中的 `"close"` 与实际 `msgType` `"closeCamera"` 也不匹配。

漏推：集成在收到后不会按报警子类型丢弃；若未出现在 HA，更可能是设备未开启或开放平台未推送该 `msgType`。本次通过可观测性支持自证，不保证平台一定推送。

参考：

- [Event message type definition](https://open.imoulife.com/book/en/push/alarm.html)
- [Event message format definition](https://open.imoulife.com/book/en/push/event.html)
- [setMessageCallback](https://open.imoulife.com/book/en/http/push/setMessageCallback.html)

## 目标

1. `openCamera` / `closeCamera` 及同类状态/操作消息：只发 `imou_life_event`，不发 `imou_life_alarm`，不触发 notify。
2. 真报警（`videoMotion`、`human`、`mobileDetect`、`abAlarmSound`、烟感等）：行为与现网一致（推送到达时两者都发）。
3. diagnostics 可展示近期收到的 `msgType` 计数与本地 push 配置，便于回复 #66。
4. README 补充 Event push 排查步骤。

## 非目标

- 不实现报警记录轮询兜底
- 不在 pyimouapi / 本集成中调用 `getMessageCallback`
- 不保证特定机型一定收到 `videoMotion` / `human`
- 不改 `callbackFlag` 语义，不新增 push 类型选项
- 不改 HA core `imou` 组件
- 不做 breaking change：`imou_life_event` / `imou_life_alarm` 名称与 payload 字段保持不变
- 计数不持久化、不做成实体

## 架构与数据流

```text
Imou cloud POST → async_handle_imou_webhook
  → normalize payload
  → push_enabled? selected_devices?
  → record runtime msgType stats
  → bus.fire(imou_life_event)
  → if is_alarm_msg_type(msg_type):
        bus.fire(imou_life_alarm)
        optional notify
  → HTTP 200
```

判定与通知共用同一 `is_alarm` 结果。

## §1 报警分类（混合）

### 规则

```text
is_alarm = msg_type is not None and msg_type not in NON_ALARM_MSG_TYPES
```

- 默认：未知 `msgType` **算报警**（避免漏报新安防类型）。
- `msg_type is None`：**不算报警**（避免脏 payload）。

将现有 `_NON_ALARM_TYPES` 重命名/扩展为 `NON_ALARM_MSG_TYPES`（或等价私有常量），并抽出 `_is_alarm_msg_type(msg_type: str | None) -> bool`，供 handler 与测试使用。常量旁注释指向官方类型文档。

### `NON_ALARM_MSG_TYPES` 至少包含

| 类别 | msgType |
|------|---------|
| 设备状态（原有） | `online`, `offline`, `close`, `changeDevName` |
| IoT / 统计（原有） | `iotEvent`, `iotProperty`, `iotAction`, `numberstat` |
| 隐私罩（#66） | `openCamera`, `closeCamera` |
| 灯光 / 警笛状态 | `whiteLightOn`, `whiteLightOff`, `sirenOn`, `sirenOff` |
| 休眠 | `sleep` |
| 绑定 / 分享 / 授权 / 转移 | `bindDevice`, `unbindDevice`, `deviceShare`, `deviceShareCancel`, `deviceAuthorize`, `deviceAuthorizationChanged`, `transferDeviceFrom`, `transferDeviceTo`, `deviceDeletedSharedCancel` |
| 升级 / 存储运维 | `UpgradeSuccess`, `upgradeFail`, `apUpgradeSuccess`, `apUpgradeFail`, `storageRecoverOk`, `storageRecoverFail`, `storageEmpty`, `storageAbnormal` |

**刻意保留为报警（不进黑名单）：** `videoMotion`, `human`, `mobileDetect`, `abAlarmSound`, 以及烟感 / 燃气 / 门磁等安防类。

实现时可按上表一次写入完整 frozenset；后续若 issue 再报误分类，只扩黑名单。

### 事件行为

1. 通过 push 开关与设备过滤后 → 一律 `imou_life_event`
2. `is_alarm` → 额外 `imou_life_alarm`
3. `is_alarm` 且配置了 notify → 发通知

## §2 可观测性与文档

### Runtime 计数

在 `ImouRuntimeData` 增加（进程内、不落盘）：

| 字段 | 含义 |
|------|------|
| `push_msg_type_counts: dict[str, int]` | 按 `msgType` 累加（含非报警） |
| `push_last_msg_type: str \| None` | 最近一次成功处理的类型 |
| `push_last_received_at: datetime \| None` | 最近一次成功处理时间（UTC） |

约束：

- 不同 `msgType` 键数上限 **50**；超出时计入 `"_other"`。
- 仅在通过 `push_enabled` 与设备过滤之后、准备/已经 `async_fire(EVENT_IMOU_EVENT)` 时更新。
- push 关闭或设备被过滤：**不计入**。

### Diagnostics

`async_get_config_entry_diagnostics` 增补（脱敏策略不变）：

```text
event_push:
  enabled: bool
  webhook_url_configured: bool
  event_push_types: list
  base_push: str
  selected_devices_count: int
  recent_msg_type_counts: dict
  last_msg_type: str | null
  last_received_at: str | null   # ISO8601 或 null
```

`runtime_data` 缺失时上述计数字段给空默认值，不抛错。不调用云端 callback 查询 API。

### README

在 Troubleshooting 增加 Event push 要点：

1. 自动化可同时监听 `imou_life_event` 与 `imou_life_alarm`；隐私罩开关只应出现在前者。
2. 能收到 `abAlarmSound` / `closeCamera` 说明 callback / webhook 通路正常。
3. 若始终没有 `videoMotion` / `human` / `mobileDetect`：检查设备端动检/人形开关；下载 diagnostics 查看 `recent_msg_type_counts`——若从未出现，属云端/设备未推，非 HA 分类问题。
4. 确认 Options 中 event push 已启用，且类型包含 `alarm`。

## §3 错误处理与边界

- Webhook 始终 HTTP 200；非法 JSON / 非 dict：不更新计数、不发事件。
- notify 失败：打日志，不影响事件与 200。
- 改动文件：`webhook.py`、`runtime_data.py`、`diagnostics.py`、`tests/test_webhook.py`、`tests/test_diagnostics.py`、`README.md`（必要时 `CHANGELOG.md` 在实现计划中单独列出）。
- 黑名单手工维护，不爬取官方页面。

## 测试计划

| 用例 | 期望 |
|------|------|
| `closeCamera` / `openCamera` | 有 `imou_life_event`，无 `imou_life_alarm`，不调 notify |
| `videoMotion` / `human` / `abAlarmSound` | 两者都有 |
| `iotProperty` | 保持现有：仅 generic event |
| `msgType` 缺失 | 发 `imou_life_event`，不发 `imou_life_alarm` |
| diagnostics | webhook 处理后含 `recent_msg_type_counts` |
| 未选中设备 / push 关闭 | 无事件、计数不增加 |

## 成功标准

1. #66 描述的误分类在单测与行为上已修复。
2. 真报警类型在推送到达时行为不变。
3. diagnostics 能支撑「是否收到某 msgType」的排查。
4. `script/lint-check` 与 `script/test` 通过。

## 实现顺序（供后续 plan）

1. 扩展 `NON_ALARM_MSG_TYPES` + `_is_alarm_msg_type`，改 handler 判定
2. Runtime 计数字段 + webhook 更新
3. Diagnostics 输出
4. 测试
5. README（+ 实现阶段再定是否写 CHANGELOG）

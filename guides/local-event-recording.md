# Record on alarm / 告警时录像

**[English](#english)** | **[简体中文](#zh-hans)**

---

<a id="english"></a>

## English

When an Imou **alarm** is pushed to Home Assistant, cameras whose **Record on alarm** switch is on can save a short MP4 from the **cloud HLS live stream**.

This is **post-event** recording, not an NVR. Dual-lens devices have one switch per channel.

### What you get / what you do not

| Supported | Not supported |
| --- | --- |
| Record **after** an alarm (15–180 seconds) | Reliable **pre-roll** (seconds before the alarm) |
| Save MP4 under an allowlisted path | Continuous 24/7 NVR-style recording |
| Per-camera switch (default off, stored in Home Assistant only) | Writing the switch back to the Imou cloud |
| Shared folder and duration for the whole account | Download of Imou **cloud** history clips |
| Uses existing `camera.*` entities | Local RTSP (Imou Life live view is cloud HLS) |

Each recording pulls the cloud live stream and consumes **Open Platform live-view quota**. Check [My Resources](https://open.imoulife.com/consoleNew/resourceManage/myResource) (international) or the China console equivalent.

### Prerequisites

1. **Imou Life** installed; at least one `camera.*` entity.
2. **Event push** enabled: **Configure → Alarms, notifications, and recording**, include **alarm**, and a reachable HA callback URL.
3. A **writable** directory listed in `allowlist_external_dirs` (creating a folder alone is not enough).
4. The **Stream** component. This integration asks Home Assistant to load Stream when available. If logs still say it is not set up, add `stream:` to `configuration.yaml` and restart.

### Step 1 — Create a folder and allowlist it

```bash
mkdir -p /media/imou
```

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

Merge `allowlist_external_dirs` into your existing `homeassistant:` block; do not duplicate the `homeassistant:` key.

On Core / development installs, use an absolute path under your config folder instead of `/media/imou`.

Restart Home Assistant after changing YAML.

### Step 2 — Shared folder and duration

**Settings → Devices & services → Imou Life → Configure → Alarms, notifications, and recording → Local recording**

- **Save folder** — same path as the allowlist, for example `/media/imou`. Leave empty to disable saving even if a camera switch is on.
- **Clip duration** — seconds after the alarm (default 60, range 15–180).

These settings apply to every camera on this account. They do not turn recording on by themselves.

### Step 3 — Per-camera switch

On each camera device page, open **Record on alarm** (configuration section) and turn it on for the lenses you want.

Default is off so an alarm does not start pulling live streams for every device.

### Step 4 — Confirm a clip

Wait until the camera entity is not `unavailable`. Trigger a real alarm (or wait for one). A file like `/media/imou/<deviceId>_<channel>_<timestamp>.mp4` should appear.

To test `camera.record` without waiting for a push:

```yaml
action: camera.record
target:
  entity_id: camera.YOUR_CAMERA_ENTITY
data:
  filename: /media/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

Set `lookback: 0`. Pre-roll is not reliable on cloud HLS.

### Optional — your own automation

The built-in switch already calls `camera.record` on `imou_life_alarm`. Keep a YAML automation only if you need extra filters or a different filename. Use the same allowlisted root.

### Limitations

1. **Cloud HLS only** — same source as live preview; latency of several seconds is normal.
2. **Post-event only** — no dependable pre-alarm buffer without keeping a continuous stream open.
3. **Quota** — every clip consumes live-stream quota for the AppId.
4. **Encrypted alarm images** in push payloads cannot be used as thumbnails; use `camera.snapshot` if you need a still for notifications.
5. Overlapping alarms on the same camera are skipped until the current clip duration elapses.

### Troubleshooting

| Symptom | What to do |
| --- | --- |
| Switch on but no file | Confirm **Configure → Alarms, notifications, and recording → Local recording** has a folder; confirm event push and **alarm** type; confirm the camera switch is on. |
| `Stream integration is not set up` | Add `stream:` to `configuration.yaml`, restart. Stream is **not** added via the brand picker. |
| `Can't write …, no access to path!` | Folder is not under `allowlist_external_dirs`, directory missing, or absolute path mismatch. Fix and restart. Options save is refused until the folder is allowlisted. |
| `Referenced entities … missing or not currently available` | Wait until the camera state is not `unavailable` after restart; verify entity ID. |
| No `imou_life_alarm` | Enable event push + **alarm** type; fix external URL; Diagnostics → `event_push.recent_msg_type_counts`. Privacy mask (`openCamera` / `closeCamera`) fires `imou_life_event` only. |
| Never see `human` / `videoMotion` | Enable picture change / human detection on the device; confirm the cloud actually pushes those types. |
| Empty / failed MP4 | Stream URL expired, network issue, or quota; retry; check logs around `getLiveStreamInfo` / stream / ffmpeg. |

### Related

- [README — Event push & automations](../README.md#features)
- [Troubleshooting in README](../README.md#troubleshooting)

---

<a id="zh-hans"></a>

## 简体中文

乐橙**告警**推送到 Home Assistant 后，打开了 **告警时录像** 开关的摄像头会从**云端 HLS 直播流**保存一段短 MP4。

这是**事后短视频**，不是 NVR。双目设备按通道各有一个开关。

### 能做什么 / 不能做什么

| 支持 | 不支持 |
| --- | --- |
| 告警**之后**录一段（15–180 秒） | 可靠的**预录**（告警前若干秒） |
| 将 MP4 写到白名单目录 | 7×24 持续 NVR 式录像 |
| 每路镜头一个开关（默认关，只存在 Home Assistant） | 把开关写回乐橙云 |
| 整个账号共用保存目录和时长 | 下载乐橙**云端历史**录像片段 |
| 复用 Imou Life 已有的 `camera.*` 实体 | 局域网 RTSP（当前直播为云端 HLS） |

每次录制都会拉云端直播，消耗开放平台**直播配额**。请在控制台查看「我的资源」：[国际站](https://open.imoulife.com/consoleNew/resourceManage/myResource) 或中国站对应页面。

### 前置条件

1. 已安装 **Imou Life**，且存在 `camera.*` 实体。
2. 已开启**事件推送**：**配置 → 告警、通知与录像**，包含 **alarm**，且回调 URL 可被乐橙云访问。
3. 有一个**可写**目录，并已写入 `allowlist_external_dirs`（只建文件夹不够）。
4. **Stream** 组件。本集成会在可用时请求 Home Assistant 加载 Stream。若日志仍提示未启用，在 `configuration.yaml` 增加 `stream:` 后重启。

### 步骤 1 — 创建目录并加入白名单

```bash
mkdir -p /media/imou
```

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

请把 `allowlist_external_dirs` **合并**进已有的 `homeassistant:` 段，不要重复写两个 `homeassistant:`。

Core / 开发环境请改用配置目录下的绝对路径，不要照搬 `/media/imou`。

改 YAML 后重启 Home Assistant。

### 步骤 2 — 公用保存目录和时长

**设置 → 设备与服务 → Imou Life → 配置 → 告警、通知与录像 → 本地录像**

- **保存目录** — 与白名单相同，例如 `/media/imou`。留空则即使打开了摄像头开关也不会保存。
- **片段时长** — 告警后录制的秒数（默认 60，范围 15–180）。

这两项对账号下所有摄像头共用，本身不会打开录像。

### 步骤 3 — 每路镜头开关

在各摄像头设备页打开 **告警时录像**（配置区），只给需要的镜头打开。

默认关闭，避免一次告警把账号下所有设备都拉去直播。

### 步骤 4 — 确认生成文件

等待相机实体不是 `unavailable`。触发一次真实告警后，应出现类似 `/media/imou/<deviceId>_<channel>_<时间戳>.mp4` 的文件。

不经过推送、只验证 `camera.record` 时：

```yaml
action: camera.record
target:
  entity_id: camera.YOUR_CAMERA_ENTITY
data:
  filename: /media/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

请将 `lookback` 设为 `0`。云端 HLS 无法可靠预录。

### 可选 — 自己写自动化

内置开关已在 `imou_life_alarm` 时调用 `camera.record`。只有需要额外过滤或自定义文件名时才保留 YAML 自动化。路径仍须落在同一白名单目录下。

### 限制说明

1. **仅云端 HLS** — 与实时预览同源；数秒级延迟属正常。
2. **仅事后录** — 不做持续拉流缓冲则无法可靠预录。
3. **配额** — 每段录像都会消耗该 AppId 的直播配额。
4. 推送中的**告警图片为加密格式**，不能直接当缩略图；通知配图请用 `camera.snapshot`。
5. 同一摄像头在当前片段时长内的重复告警会被跳过。

### 故障排查

| 现象 | 处理 |
| --- | --- |
| 开关已开但没有文件 | 确认 **配置 → 告警、通知与录像 → 本地录像** 已填目录；确认已启用事件推送且包含 **alarm**；确认该路镜头开关已开。 |
| `Stream integration is not set up` | 在 `configuration.yaml` 增加 `stream:` 并重启。Stream **不能**通过「添加集成」品牌列表安装。 |
| `Can't write …, no access to path!` | 目录不在 `allowlist_external_dirs` 下、文件夹不存在、或绝对路径不一致。修正后重启。未加入白名单时，选项页会拒绝保存。 |
| `Referenced entities … missing or not currently available` | 重启后等待相机状态非 `unavailable`；核对实体 ID。 |
| 收不到 `imou_life_alarm` | 启用事件推送且包含 **alarm**；检查外网 URL；诊断信息中的 `event_push.recent_msg_type_counts`。隐私遮蔽（`openCamera` / `closeCamera`）只触发 `imou_life_event`。 |
| 始终没有 `human` / `videoMotion` | 在设备上开启画面变化/人形检测；确认云端确实推送了这些类型。 |
| MP4 为空或失败 | 流地址过期、网络或配额问题；重试；查看 `getLiveStreamInfo` / stream / ffmpeg 相关日志。 |

### 相关链接

- [README — 事件推送与自动化](../README.md#zh-hans)
- [README 故障排查](../README.md#故障排查)

# Local event recording (workaround) / 本地事件录像（临时方案）

**[English](#english)** | **[简体中文](#zh-hans)**

---

<a id="english"></a>

## English

> **Status:** Imou Life does **not** yet provide built-in local recording.  
> Use this Home Assistant workaround today. **Native support is planned for a future community release** (service and/or optional auto-record on alarm). Details will be announced in the [CHANGELOG](../CHANGELOG.md) / GitHub Releases when available.

This guide covers **post-event** clips: when an Imou alarm fires, record a short segment from the **cloud HLS live stream** to a folder on the Home Assistant host (or NAS mount).

### What you get / what you do not

| Supported by this workaround | Not supported |
| --- | --- |
| Record **after** an alarm (e.g. 30–120 seconds) | Reliable **pre-roll** (seconds before the alarm) |
| Save MP4 under an allowlisted path | Continuous 24/7 NVR-style recording |
| Trigger via `imou_life_alarm` (webhook) or a manual test | Download of Imou **cloud** history clips |
| Uses existing `camera.*` entities from Imou Life | Local RTSP (Imou Life live view is cloud HLS) |

Each recording pulls the cloud live stream and consumes **Open Platform live-view quota**. Check [My Resources](https://open.imoulife.com/consoleNew/resourceManage/myResource) (international) or the China console equivalent.

### Prerequisites

1. **Imou Life** installed and configured; at least one `camera.*` entity (Developer tools → States).
2. Home Assistant can edit `configuration.yaml` and restart.
3. A **writable** directory for MP4 files (examples below).
4. *(Optional, for real alarms)* Event push enabled: **Configure → Event push**, include **alarm**, and a reachable HA callback URL (public URL / Nabu Casa / tunnel). Without a public URL you can still verify recording with a manual `camera.record` call or by firing a test event.

### Step 1 — Enable the Stream integration

`camera.record` requires Home Assistant’s core **Stream** component. It does **not** appear in **Settings → Devices & services → Add integration** (searching “Stream” only shows unrelated brands).

Add to `configuration.yaml`:

```yaml
stream:
```

Save the file. You will restart in Step 3.

### Step 2 — Create a folder and allowlist it

Home Assistant may only write recording files under paths listed in `allowlist_external_dirs`. Creating a folder alone is not enough.

**`filename` in Steps 4–5 must use the same directory you allowlist here.**

#### Example A — Home Assistant OS / Supervised (typical `/media`)

```bash
mkdir -p /media/imou
```

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

Recording paths must start with `/media/imou/`, for example:

- `/media/imou/test_20260810_153000.mp4`
- `/media/imou/{{ trigger.event.data.device_id }}_20260810_153000.mp4`

#### Example B — Core / development (`python -m homeassistant -c config`)

Use a directory under your config folder (replace with your machine’s absolute path):

```bash
mkdir -p /path/to/config/www/imou
```

```yaml
homeassistant:
  name: Dev   # keep your existing keys
  allowlist_external_dirs:
    - /path/to/config/www/imou
```

Recording paths must start with `/path/to/config/www/imou/`, for example:

- `/path/to/config/www/imou/test_20260810_153000.mp4`

Merge `allowlist_external_dirs` into your existing `homeassistant:` block; do not duplicate the `homeassistant:` key.

### Step 3 — Check config and restart

1. **Developer tools → YAML → Check configuration**
2. **Restart Home Assistant**
3. Wait until Imou camera entities are no longer `unavailable` (**Developer tools → States**). Calling `camera.record` too soon after restart often yields:  
   `Referenced entities camera.… are missing or not currently available`.

### Step 4 — Manual recording test

1. Open **Developer tools → Actions** (formerly “Services”).
2. Call `camera.record` with your real entity ID.
3. Set `filename` to a path **under the same allowlisted directory from Step 2**.

**If you used Example A (`/media/imou`):**

```yaml
action: camera.record
target:
  entity_id: camera.YOUR_CAMERA_ENTITY
data:
  filename: /media/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

**If you used Example B (`/path/to/config/www/imou`):**

```yaml
action: camera.record
target:
  entity_id: camera.YOUR_CAMERA_ENTITY
data:
  filename: /path/to/config/www/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

Notes:

- Set `lookback: 0`. Pre-roll is not reliable on cloud HLS.
- Find the entity under **Settings → Devices & services → Imou Life → device → entities**, or States search `camera.`.
- Dual-lens devices may expose two cameras (e.g. PTZ vs fixed). Pick the correct `entity_id`.

After ~30 seconds, confirm the MP4 exists on disk (or Media Browser if your path is exposed there).

### Step 5 — Automation on Imou alarms

#### 5.1 Enable event push (real alarms)

**Settings → Devices & services → Imou Life → Configure → Event push**

- Enable event push
- Include message type **alarm**
- Ensure the suggested callback URL is reachable from the Imou cloud (or set a custom public callback URL)

HA events:

- `imou_life_event` — all accepted pushes  
- `imou_life_alarm` — security / alarm-type messages only  

Useful payload fields: `device_id`, `channel_id`, `msg_type` (e.g. `human`, `videoMotion`, `mobileDetect`).

#### 5.2 Example automation

Again, keep `filename` on the **same allowlisted root** as Step 2.

**Example A (`/media/imou`):**

```yaml
alias: Imou alarm — save local clip
description: Post-event record via camera.record (cloud HLS)
mode: single
max_exceeded: silent
triggers:
  - trigger: event
    event_type: imou_life_alarm
    # Optional filters once you know payload values:
    # event_data:
    #   device_id: "YOUR_DEVICE_ID"
    #   msg_type: human
actions:
  - action: camera.record
    target:
      entity_id: camera.YOUR_CAMERA_ENTITY
    data:
      filename: /media/imou/{{ trigger.event.data.device_id }}_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
      duration: 60
      lookback: 0
```

**Example B (`/path/to/config/www/imou`):** same automation, but:

```yaml
filename: /path/to/config/www/imou/{{ trigger.event.data.device_id }}_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
```

`mode: single` drops overlapping runs. Use `queued` or `parallel` if you need overlapping clips (watch Open Platform quota).

#### 5.3 Test without a public webhook

**Developer tools → Events → Fire event:**

```yaml
event_type: imou_life_alarm
event_data:
  device_id: "test"
  msg_type: human
  channel_id: "0"
```

Confirm the automation runs and a file appears.

### Limitations (please read)

1. **Cloud HLS only** — same source as live preview; latency of several seconds is normal.
2. **Post-event only** — no dependable pre-alarm buffer without keeping a continuous stream open (extra quota / complexity; out of scope here).
3. **Quota** — every clip consumes live-stream quota for the AppId.
4. **Encrypted alarm images** in push payloads cannot be used as thumbnails; use `camera.snapshot` if you need a still for notifications.
5. This workaround is **YAML / automation based**. A first-class Imou Life feature is planned later (see status at the top).

### Troubleshooting

| Symptom | What to do |
| --- | --- |
| `Stream integration is not set up` | Add `stream:` to `configuration.yaml`, restart. Stream is **not** added via the brand picker. |
| `Can't write …, no access to path!` | `filename` is not under `allowlist_external_dirs`, directory missing, or absolute path mismatch with Step 2. Fix and restart. |
| `Referenced entities … missing or not currently available` | Wait until the camera state is not `unavailable` after restart; verify entity ID. |
| No `imou_life_alarm` | Enable event push + **alarm** type; fix external URL; Diagnostics → `event_push.recent_msg_type_counts`. Privacy mask (`openCamera` / `closeCamera`) fires `imou_life_event` only. |
| Never see `human` / `videoMotion` | Enable motion/human detection on the device; confirm the cloud actually pushes those types. |
| Empty / failed MP4 | Stream URL expired, network issue, or quota; retry; check logs around `getLiveStreamInfo` / stream / ffmpeg. |

### Roadmap — native support

We plan to productize this flow in a **later Imou Life version**, for example:

- A dedicated action/service (e.g. record clip for a camera entity)
- Optional “auto-record on alarm” settings (duration, path, message types)

Until then, this document is the supported customer / self-host recipe. Track progress via GitHub Issues / Releases on [Imou-Home-Assistant](https://github.com/Imou-OpenPlatform/Imou-Home-Assistant).

### Related

- [README — Event push & automations](../README.md#features)
- [Troubleshooting in README](../README.md#troubleshooting)

---

<a id="zh-hans"></a>

## 简体中文

> **现状：** Imou Life **尚未**提供内置本地录像能力。  
> 请先按本文用 Home Assistant 现有能力落地。**社区集成后续版本计划提供原生支持**（例如录制服务，和/或告警自动录制选项）。具体说明将在 [CHANGELOG](../CHANGELOG.md) / GitHub Release 中公布。

本文说明的是**事后录制**：乐橙告警触发后，从**云端 HLS 直播流**录一段短视频，保存到 Home Assistant 主机（或挂载的 NAS）目录。

### 能做什么 / 不能做什么

| 本方案支持 | 不支持 |
| --- | --- |
| 告警**之后**录一段（如 30–120 秒） | 可靠的**预录**（告警前若干秒） |
| 将 MP4 写到白名单目录 | 7×24 持续 NVR 式录像 |
| 用 `imou_life_alarm`（Webhook）或手动测试触发 | 下载乐橙**云端历史**录像片段 |
| 复用 Imou Life 已有的 `camera.*` 实体 | 局域网 RTSP（当前直播为云端 HLS） |

每次录制都会拉云端直播，消耗开放平台**直播配额**。请在控制台查看「我的资源」：[国际站](https://open.imoulife.com/consoleNew/resourceManage/myResource) 或中国站对应页面。

### 前置条件

1. 已安装并配置 **Imou Life**，且存在 `camera.*` 实体（开发者工具 → 状态）。
2. 可以编辑 `configuration.yaml` 并重启 Home Assistant。
3. 有一个**可写**目录用于存放 MP4（见下文示例）。
4. *（可选，真实告警）* 已开启事件推送：**配置 → 事件推送**，包含 **alarm**，且回调 URL 可被乐橙云访问（公网 / Nabu Casa / 隧道等）。若暂无公网，仍可用手动 `camera.record` 或手动触发测试事件验证录像链路。

### 步骤 1 — 启用 Stream 集成

`camera.record` 依赖 Home Assistant 核心组件 **Stream**。它**不会**出现在「设置 → 设备与服务 → 添加集成」的品牌搜索中（搜 Stream 只会看到 StreamLabs 等无关项）。

在 `configuration.yaml` 中增加：

```yaml
stream:
```

保存文件；重启放在步骤 3。

### 步骤 2 — 创建目录并加入白名单

录制文件只能写入 `allowlist_external_dirs` 中声明的路径。只建文件夹、不写白名单会报 `no access to path`。

**步骤 4–5 中的 `filename` 必须与这里白名单目录一致。**

#### 示例 A — Home Assistant OS / Supervised（常用 `/media`）

```bash
mkdir -p /media/imou
```

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

录像路径必须以 `/media/imou/` 开头，例如：

- `/media/imou/test_20260810_153000.mp4`
- `/media/imou/{{ trigger.event.data.device_id }}_20260810_153000.mp4`

#### 示例 B — Core / 开发环境（`python -m homeassistant -c config`）

在配置目录下建目录（请改成你机器上的绝对路径）：

```bash
mkdir -p /path/to/config/www/imou
```

```yaml
homeassistant:
  name: Dev   # 保留你已有的其它字段
  allowlist_external_dirs:
    - /path/to/config/www/imou
```

录像路径必须以 `/path/to/config/www/imou/` 开头，例如：

- `/path/to/config/www/imou/test_20260810_153000.mp4`

请把 `allowlist_external_dirs` **合并**进已有的 `homeassistant:` 段，不要重复写两个 `homeassistant:`。

### 步骤 3 — 检查配置并重启

1. **开发者工具 → YAML → 检查配置**
2. **重启 Home Assistant**
3. 等到 Imou 相机实体不再是 `unavailable`（**开发者工具 → 状态**）。重启后立刻调用 `camera.record` 常见报错：  
   `Referenced entities camera.… are missing or not currently available`。

### 步骤 4 — 手动录制验证

1. 打开 **开发者工具 → 操作**（旧版界面名为「服务」）。
2. 调用 `camera.record`，填入真实实体 ID。
3. `filename` 必须落在**步骤 2 已加入白名单的同一目录**下。

**若使用示例 A（`/media/imou`）：**

```yaml
action: camera.record
target:
  entity_id: camera.你的相机实体
data:
  filename: /media/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

**若使用示例 B（`/path/to/config/www/imou`）：**

```yaml
action: camera.record
target:
  entity_id: camera.你的相机实体
data:
  filename: /path/to/config/www/imou/test_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
  duration: 30
  lookback: 0
```

说明：

- 请使用 `lookback: 0`。云端 HLS 无法可靠预录。
- 实体可在「设置 → 设备与服务 → Imou Life → 某设备 → 实体」查看，或在状态页搜索 `camera.`。
- 双目设备可能有两个相机实体（如移动镜头 / 固定镜头），请选对 `entity_id`。

约 30 秒后确认磁盘上已生成 MP4（若路径被媒体浏览器暴露，也可在媒体中查看）。

### 步骤 5 — 告警自动化

#### 5.1 开启事件推送（真实告警）

**设置 → 设备与服务 → Imou Life → 配置 → 事件推送**

- 启用事件推送
- 消息类型包含 **alarm**
- 确保建议的回调 URL 可被乐橙云访问（或填写自定义公网回调地址）

HA 事件：

- `imou_life_event` — 所有已接受推送  
- `imou_life_alarm` — 仅安防 / 告警类  

常用字段：`device_id`、`channel_id`、`msg_type`（如 `human`、`videoMotion`、`mobileDetect`）。

#### 5.2 自动化示例

`filename` 同样必须与步骤 2 的白名单根目录一致。

**示例 A（`/media/imou`）：**

```yaml
alias: Imou 告警 — 本地保存片段
description: 事后录制（camera.record + 云端 HLS）
mode: single
max_exceeded: silent
triggers:
  - trigger: event
    event_type: imou_life_alarm
    # 确认载荷后再收紧过滤条件，例如：
    # event_data:
    #   device_id: "你的设备ID"
    #   msg_type: human
actions:
  - action: camera.record
    target:
      entity_id: camera.你的相机实体
    data:
      filename: /media/imou/{{ trigger.event.data.device_id }}_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
      duration: 60
      lookback: 0
```

**示例 B（`/path/to/config/www/imou`）：** 自动化其它部分相同，仅改：

```yaml
filename: /path/to/config/www/imou/{{ trigger.event.data.device_id }}_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
```

`mode: single` 会在上一次未完成时丢弃新触发。若需重叠录制，可改为 `queued` 或 `parallel`（注意配额）。

#### 5.3 无公网 Webhook 时的测试

**开发者工具 → 事件 → 触发事件：**

```yaml
event_type: imou_life_alarm
event_data:
  device_id: "test"
  msg_type: human
  channel_id: "0"
```

确认自动化执行并生成文件即可。

### 限制说明（请阅读）

1. **仅云端 HLS** — 与实时预览同源；数秒级延迟属正常。
2. **仅事后录** — 不做持续拉流缓冲则无法可靠预录（额外配额与复杂度，不在本文范围）。
3. **配额** — 每段录像都会消耗该 AppId 的直播配额。
4. 推送中的**告警图片为加密格式**，不能直接当缩略图；通知配图请用 `camera.snapshot`。
5. 当前为 **YAML / 自动化** 临时方案；后续版本计划提供 Imou Life 原生能力（见文首状态说明）。

### 故障排查

| 现象 | 处理 |
| --- | --- |
| `Stream integration is not set up` | 在 `configuration.yaml` 增加 `stream:` 并重启。Stream **不能**通过「添加集成」品牌列表安装。 |
| `Can't write …, no access to path!` | `filename` 不在 `allowlist_external_dirs` 下、目录不存在、或与步骤 2 的绝对路径不一致。修正后重启。 |
| `Referenced entities … missing or not currently available` | 重启后等待相机状态非 `unavailable`；核对实体 ID。 |
| 收不到 `imou_life_alarm` | 启用事件推送且包含 **alarm**；检查外网 URL；诊断信息中的 `event_push.recent_msg_type_counts`。隐私遮蔽（`openCamera` / `closeCamera`）只触发 `imou_life_event`。 |
| 始终没有 `human` / `videoMotion` | 在设备上开启移动/人形侦测；确认云端确实推送了这些类型。 |
| MP4 为空或失败 | 流地址过期、网络或配额问题；重试；查看 `getLiveStreamInfo` / stream / ffmpeg 相关日志。 |

### 路线图 — 原生支持

计划在**后续 Imou Life 版本**中产品化本流程，例如：

- 专用动作 / 服务（按相机实体录制片段）
- 可选「告警自动录制」配置（时长、路径、消息类型等）

在此之前，本文即为面向客户与自建环境的推荐做法。进度请关注 [Imou-Home-Assistant](https://github.com/Imou-OpenPlatform/Imou-Home-Assistant) 的 Issue / Release。

### 相关链接

- [README — 事件推送与自动化](../README.md#zh-hans)
- [README 故障排查](../README.md#故障排查)

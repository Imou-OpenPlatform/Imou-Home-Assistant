# Record on alarm / 告警时录像

**[English](#english)** | **[简体中文](#zh-hans)**

---

<a id="english"></a>

## English

When an Imou **alarm** is pushed to Home Assistant, cameras whose **Record on alarm** switch is on save a short MP4 from the **cloud HLS live stream**. The integration does this itself — you do not write an automation.

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

### Setup

1. **Enable event push** — **Configure → Alarm push and notifications**, include **alarm**, and a callback URL the Imou cloud can reach. See [Configure reference](configuration.md#english).
2. **Pick a writable folder** and add it to Home Assistant’s **allowlist_external_dirs** (creating the folder alone is not enough). On Home Assistant OS, `/media/imou` is typical:

   ```yaml
   homeassistant:
     allowlist_external_dirs:
       - /media/imou
   ```

   Merge that into your existing `homeassistant:` block, then restart. Core / development installs should use an absolute path under the config folder instead.
3. **Configure → Record on alarm** — **Save folder** (same path as the allowlist; leave empty to skip saving) and **Clip duration** (default 60 seconds, range 15–180). These apply to every camera on this account and do not turn recording on by themselves.
4. On each camera device page, turn on **Record on alarm** (configuration section) for the lenses you want. Default is off so one alarm does not start pulling live streams for every device.

After a real alarm, a file like `/media/imou/<deviceId>_<channel>_<timestamp>.mp4` should appear. Wait until the camera entity is not `unavailable`.

### Limitations

1. **Cloud HLS only** — same source as live preview; latency of several seconds is normal.
2. **Post-event only** — no dependable pre-alarm buffer without keeping a continuous stream open.
3. **Quota** — every clip consumes live-stream quota for the AppId.
4. Overlapping alarms on the same camera are skipped until the current clip duration elapses.

### Troubleshooting

| Symptom | What to do |
| --- | --- |
| Switch on but no file | Confirm **Configure → Record on alarm** has a folder; confirm event push and **alarm** type; confirm the camera switch is on. |
| Options save refused / path not allowed | Folder is not under `allowlist_external_dirs`, directory missing, or absolute path mismatch. Fix and restart. |
| Camera unavailable | Wait until the camera state is not `unavailable` after restart. |
| No `imou_life_alarm` | Enable event push + **alarm** type; fix the external URL; Diagnostics → `event_push.recent_msg_type_counts`. Privacy mask (`openCamera` / `closeCamera`) fires `imou_life_event` only. |
| Empty / failed MP4 | Stream URL expired, network issue, or quota; retry; check logs around `getLiveStreamInfo` / stream / ffmpeg. On Home Assistant Core, if logs say Stream is not set up, add `stream:` to `configuration.yaml` and restart. |

### Related

- [Configure reference](configuration.md#english)
- [README](../README.md#english)

---

<a id="zh-hans"></a>

## 简体中文

乐橙**告警**推送到 Home Assistant 后，打开了 **告警时录像** 开关的摄像头会从**云端 HLS 直播流**保存一段短 MP4。集成自己完成，**不用写自动化**。

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

### 怎么开

1. **打开事件推送** — **配置 → 告警推送与通知**，订阅含 **alarm**，回调地址乐橙云能访问到。见 [配置项参考](configuration.md#zh-hans)。
2. **准备一个可写目录**，并加入 Home Assistant 的 **allowlist_external_dirs**（只建文件夹不够）。Home Assistant OS 上常用 `/media/imou`：

   ```yaml
   homeassistant:
     allowlist_external_dirs:
       - /media/imou
   ```

   把这段**合并**进已有的 `homeassistant:`，然后重启。Core / 开发环境请用配置目录下的绝对路径。
3. **配置 → 告警时录像** — **保存目录**（与白名单相同；留空则不保存）和 **片段时长**（默认 60 秒，范围 15–180）。这两项对账号下所有摄像头共用，本身不会打开录像。
4. 在各摄像头设备页打开 **告警时录像**（配置区），只给需要的镜头打开。默认关闭，避免一次告警把账号下所有设备都拉去直播。

真实告警之后应出现类似 `/media/imou/<deviceId>_<channel>_<时间戳>.mp4` 的文件。等相机实体不是 `unavailable` 再测。

### 限制说明

1. **仅云端 HLS** — 与实时预览同源；数秒级延迟属正常。
2. **仅事后录** — 不做持续拉流缓冲则无法可靠预录。
3. **配额** — 每段录像都会消耗该 AppId 的直播配额。
4. 同一摄像头在当前片段时长内的重复告警会被跳过。

### 故障排查

| 现象 | 处理 |
| --- | --- |
| 开关已开但没有文件 | 确认 **配置 → 告警时录像** 已填目录；确认已启用事件推送且包含 **alarm**；确认该路镜头开关已开。 |
| 选项保存被拒 / 路径不在白名单 | 目录不在 `allowlist_external_dirs`、文件夹不存在、或绝对路径不一致。修正后重启。 |
| 相机不可用 | 重启后等待相机状态非 `unavailable`。 |
| 收不到 `imou_life_alarm` | 启用事件推送且包含 **alarm**；检查外网 URL；诊断信息中的 `event_push.recent_msg_type_counts`。隐私遮蔽（`openCamera` / `closeCamera`）只触发 `imou_life_event`。 |
| MP4 为空或失败 | 流地址过期、网络或配额问题；重试；查看 `getLiveStreamInfo` / stream / ffmpeg 相关日志。Home Assistant Core 若日志说 Stream 未启用，在 `configuration.yaml` 增加 `stream:` 后重启。 |

### 相关链接

- [配置项参考](configuration.md#zh-hans)
- [README](../README.md#zh-hans)

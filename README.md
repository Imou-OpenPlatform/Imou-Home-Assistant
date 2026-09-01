# Imou Life Home Assistant Integration

**[English](#english)** | **[简体中文](#zh-hans)**

**Guides / 文档:** [Configure reference / 配置项参考](guides/configuration.md)

[![HACS Default][hacs-badge]][hacs-url]
[![GitHub Release][release-badge]][release-url]
[![HACS Downloads][downloads-badge]][release-url]
[![Active Installs][installs-badge]][analytics-url]

<a id="english"></a>

## Introduction

This integration connects Home Assistant to Imou cameras and smart devices through the Imou Open Platform API: live video, device control, and status. You can extend it with automations and additional features.

> **Open Platform portal:** [open.imoulife.com](https://open.imoulife.com/) — China users, see [简体中文](#zh-hans).

## Documentation

README covers install and a feature list. Options live in the [Configure reference](guides/configuration.md): every **Configure** page, Home Assistant URL, decrypt libraries, **Record on alarm**, and what depends on what.

## Installation

Register on the **international** Open Platform, then install via HACS.

### 1–2. Register & create AppId / AppSecret

| | |
| --- | --- |
| Portal | [open.imoulife.com](https://open.imoulife.com/) |
| Console | [My App](https://open.imoulife.com/consoleNew/myApp/appInfo) |
| Server region (HA login) | Singapore / Europe / North America (see [API domains](https://open.imoulife.com/book/http/develop.html)) |
| API domain doc | [develop.html](https://open.imoulife.com/book/http/develop.html) |
| My Resources | [Resource manage](https://open.imoulife.com/consoleNew/resourceManage/myResource) |

Register on [open.imoulife.com](https://open.imoulife.com/), then open **My App** in the console to create an application and obtain **AppId** and **AppSecret**.

<img src="assets/images/appMsg.png" width="70%" alt="Imou Open Platform — create AppId and AppSecret">

### 3. Install via HACS

<b>Navigate to HACS, search for `Imou Life`, and install the integration.</b> On the login page, enter your App ID and App secret, and select the **server region** closest to your account (**Europe / North America / Singapore**). The region must match the international portal where the app was created.

<img src="assets/images/login_new.png" width="70%" alt="Imou Life login — App ID, App Secret, and server region">

Then select which devices to add. Unselected devices stay in Home Assistant but are not polled (saves API quota).

### 4. Done

Devices under your Imou account should appear in Home Assistant.

<img src="assets/images/integration_overview.png" width="70%" alt="Imou Life integration entry and entities">

Use **Configure** on the integration entry. The menu is **Polling and cameras**, **Alarm push and notifications**, **Alarm pictures**, **Record on alarm**, **Choose devices to poll**, and **Bind a new device**. Each page saves and returns to the menu; **Done** closes it.

Page-by-page options, including the **Settings → System → Network** steps that alarm push and alarm pictures need, and **Record on alarm**: [Configure reference](guides/configuration.md#english).

>Note: <br>
>The integration uses the Imou Open Platform for cloud-based remote device access. <br>
>Cloud API calls and video playback consume the resource quota of your AppId account — check **My Resources** in the [international console](https://open.imoulife.com/consoleNew/resourceManage/myResource).

### Obtain the official decrypt libraries

The integration does not ship the native libraries that decrypt alarm pictures. **For now, download them yourself.** linux x86-64 only.

| Region | Download |
| --- | --- |
| China | [openapi.lechange.cn … resourceType=ImageDecrpt](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt) |
| Overseas | [openapi.easy4ip.com … resourceType=HTTPInterfaceCallDemo](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo) |

The link yields a zip. After you extract it, the two libraries are under `Open-PicDecode/src/main/resources/linux-x86-64`. Copy **both** `libLCOpenApiClient.so` and `libLCOpenSDK.so` into `/config/imou_life/native/`. One file alone will not load.

## Features
* **Integration & account**
  - Bind devices to your open-platform account from **Configure → Bind a new device** (device serial + binding code); setup no longer aborts when the account has no devices yet (bind now or finish with an empty selection)
  - Device selection at setup and in **Configure → Choose devices to poll** (poll only chosen devices)
  - **Configure** menu: **Polling and cameras**, **Alarm push and notifications**, **Alarm pictures**, **Record on alarm**, **Choose devices to poll**, and **Bind a new device**; each section saves independently and hands you back to the menu, which shows what is currently on
  - Login aligned with Home Assistant Core: **server region** dropdown (Europe / North America / Singapore)
  - UI available in English, Simplified Chinese, German, French, and Italian (follows Home Assistant language)
  - Built on [pyimouapi](https://pypi.org/project/pyimouapi/) 1.4.1 for Open Platform API access
* **Event push & automations**
  - Optional webhook callback for real-time messages from Imou cloud (requires public HA URL or manual callback URL)
  - **Configure → Alarm push and notifications** — callback URL (suggested URL; replace hostname and port if it is not public), message types, and phone notify.
  - Home Assistant events: `imou_life_event` (all accepted pushes), `imou_life_alarm` (alarm-type only)
  - Optional alarm notifications: pick Companion App or other notify targets under **Configure → Alarm push and notifications**. Silence one device with **Notify on alarm** on its device page (default on). Companion App: tap opens that camera/accessory's Home Assistant device page
  - Optional **Show the picture in alarm notifications** (default off, **linux x86-64 only**): Home Assistant downloads the encrypted push still and decrypts it locally. [Download the official libraries](#obtain-the-official-decrypt-libraries), then **Configure → Alarm pictures**. Many motion pushes have no picture URL; those stay text-only. Full setup: [Configure reference](guides/configuration.md#english)
  - Choose push message types—including IoT device messages so thing-model switches, sensors, and arming update from property push instead of interval polling; messages are also synced to the Imou Life app
  - **Record on alarm** — built-in: per-camera switch (default off), shared folder and duration under **Configure → Record on alarm**. No YAML automation. Setup: [Configure reference](guides/configuration.md#record-on-alarm)
* **Camera**
  - Status (name, online, storage, battery, …)
  - Live video
  - PTZ (direction buttons; duration in **Configure → Polling and cameras → Camera defaults**)
  - **Collection points** — `select.collection_point` (**Go to collection point**) lists points from the device / Imou Life app; choose one to move the camera (needs `CollectionPoint`; current position is not read back)
  - Detection: picture change, human, pet
  - **Motion** (`binary_sensor`, `device_class: motion`) — on cameras that support picture-change or human detection. On for about 15 seconds after a picture-change / human / PIR / person-in-area / line-crossing / area-intrusion push, or off immediately on PIR-clear. Pet / vehicle alarms do not drive it. Home Assistant restart resets it to off. Needs **Enable event push** with type **alarm**; if push is off the entity stays but is unavailable. Distinct from the **Picture change** / **Human detection** switches (those enable detection; this reports a detection)
  - **Doorbell** (`event`, `device_class: doorbell`) — on cameras that support calling. A press or incoming call from event push fires `ring`. Unanswered calls do not. Needs **Enable event push** with type **alarm**; if push is off the entity stays but is unavailable
  - **Alarm picture** (`image`) — last decrypted alarm still for that lens. Pin it on a dashboard. Needs **Enable event push** with type **alarm** and **Show the picture in alarm notifications**; if either is off the entity stays but is unavailable. Empty until a push includes a picture this Home Assistant can decrypt. Does not take a live snapshot
  - Privacy mode, night vision, flip image, wide dynamic range, smart tracking
  - White light, alarm-linked white light, alarm-linked siren (configuration switches)
  - Manual siren via `siren` entity (`siren.turn_on` / `siren.turn_off`) on devices with Siren capability or IoT refs `25500`/`22200`; does not require event push for manual control. Entity state auto-clears after about 15 seconds (typical firmware hold), or immediately on a `sirenOff` push when event push is enabled
  - Audio recording, prompt sound, abnormal sound alarm
  - Restart device
* **Alarm sensors**
  - Status (name, online, battery, …)
  - Arming — `alarm_control_panel` (home / away / disarm)
  - Alarm volume
  - One-tap mute
  - Indicator light
  - Temperature and humidity
  - Restart device
* **Energy devices**
  - Status (name, energy use, online)
  - Plug switch and countdown
  - Plug indicator light
  - Max power

## Troubleshooting

- **What does this option do / how do I set it?** — See [Configure reference](guides/configuration.md#english) for every option page by page, and for the **Settings → System → Network** steps that alarm push and alarm pictures both depend on.
- **Cannot enable alarm pictures / decrypt libraries missing** — Download the official Demo zip yourself ([China](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt) / [overseas](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo)), extract it, and copy both `.so` files from `Open-PicDecode/src/main/resources/linux-x86-64` into `/config/imou_life/native/`. See [Obtain the official decrypt libraries](#obtain-the-official-decrypt-libraries).
- **Invalid App ID / App secret** — Home Assistant opens a **re-authentication** flow; enter a new App secret under **Settings → Devices & services → Imou Life** (notification or three-dot menu → **Re-authenticate**).
- **Event push not working** — Open **Configure → Alarm push and notifications**. Confirm **Enable event push** is on. **Callback URL** comes prefilled with the generated address; change its hostname and port if that address is not reachable from the internet. Also check **Settings → System → Network → Home Assistant URL**. Review repair issues under **Settings → System → Repairs**.
  - Automations can listen to `imou_life_event` (all accepted pushes) and `imou_life_alarm` (security alarms only). Privacy-mask messages (`openCamera` / `closeCamera`) fire only `imou_life_event`.
  - If you receive `abAlarmSound` or `closeCamera`, the callback/webhook path is working.
  - If `videoMotion` / `human` / `mobileDetect` never appear: confirm picture change / human detection is enabled on the device; download **Diagnostics** and check `event_push.recent_msg_type_counts`. Missing keys mean the cloud/device did not push those types (not an HA misclassification). A push that does not match a Home Assistant device is discarded (still HTTP 200).
  - Confirm event push is enabled in **Configure** and push types include **alarm**.
  - If you do not see **Doorbell**: that camera does not support calling. If the entity is **unavailable**: turn on event push and include type **alarm**. Picture-change / human detection is a different entity (**Motion**).
  - If you do not see **Alarm picture**: that device is not a camera. If the entity is **unavailable**: turn on event push with type **alarm**, and **Configure → Alarm pictures → Show the picture in alarm notifications**. If it is available but empty: the last alarm had no picture, or decrypt did not succeed. The entity does not take a live snapshot.
  - If you do not see **Motion**: that camera does not support picture-change or human detection. If the entity is **unavailable**: turn on event push and include type **alarm**. If it is available but never turns on: download **Diagnostics** and check `event_push.recent_msg_type_counts`. Newer IoT PTZ cameras often push `e_multiVideoAiPerArea` / `e_smartMixDetect` / `e_areaDetect` / `crossLineDetection` rather than `human` / `videoMotion` — those now drive this entity. Vehicle / pet still do not. The sensor does not poll the cloud.
  - If thing-model switches or sensors freeze after a reload: event push must be on and types must include **IoT device messages**. Download **Diagnostics** and check `event_push.recent_msg_type_counts` for `iotProperty`. Uncheck that type to resume property polling.
- **Automations after upgrade** — v1.3.0 removes custom `imou_life.turn_on` / `turn_off` / `select` services; use standard `switch.turn_on`, `select.select_option`, and `button.press`. v1.4.0 replaces `select.select_option` on `select.*_mode` with `alarm_control_panel.alarm_arm_home` / `alarm_arm_away` / `alarm_disarm`, and `button.siren_start` / `siren_stop` with `siren.turn_on` / `siren.turn_off`. Leftover `select.*_mode` and siren button registry rows are removed on setup.
- **PTZ collection points** — use `select.select_option` on `select.<device>_collection_point` with the collection point name (as shown in the Imou Life app). The select shows **Select a collection point…** when the camera is not at a known position.
- **Diagnostics** — Download redacted diagnostics from the integration's **Download diagnostics** (three-dot menu on the config entry).
- **Record on alarm — no file** — confirm event push, the per-camera switch, and an allowlisted save folder: [Configure reference](guides/configuration.md#record-on-alarm).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

- Development setup: `script/setup`
- Lint: `script/lint-check`
- Tests: `script/test`

## Statistics

| Metric | Source | Notes |
| --- | --- | --- |
| **Active installs** | [Home Assistant Analytics](https://analytics.home-assistant.io/custom_integrations.json) | Live HA instances reporting `imou_life` (opt-in analytics only) |
| **HACS downloads** | GitHub Releases | Total downloads of `imou_life.zip` (HACS install + update) |

---

<a id="zh-hans"></a>

## 简介

本集成通过 Imou 开放平台 API，把乐橙摄像头和智能设备接入 Home Assistant：直播、控制与状态查看，也可在此基础上做自动化和扩展。

> **开放平台入口：** [open.imou.com](https://open.imou.com/) — 海外用户请参阅 [English](#english)。

## 文档

README 只写安装和功能列表。选项说明在 [配置项参考](guides/configuration.md#zh-hans)：每一个 **配置** 页、Home Assistant 地址、解密库、**告警时录像**，以及依赖关系。

## 安装

在 **Imou 国内开放平台** 注册并创建应用，再通过 HACS 安装。

### 1–2. 注册并创建 AppId / AppSecret

| | |
| --- | --- |
| 平台入口 | [open.imou.com](https://open.imou.com/) |
| 控制台 | [我的应用](https://open.imou.com/consoleNew/myApp/appInfo) |
| 服务器区域（HA 登录） | **中国** |
| API 域名文档 | [develop.html](https://open.imou.com/book/http/develop.html) |
| 我的资源 | [资源管理](https://open.imou.com/consoleNew/resourceManage/myResource) |

在 [open.imou.com](https://open.imou.com/) 注册账号，进入控制台 **我的应用**，创建应用并获取 **AppId** 与 **AppSecret**。

<img src="assets/images/appMsg.png" width="70%" alt="Imou 开放平台 — 创建 AppId 与 AppSecret">

### 3. 通过 HACS 安装

<b>在 HACS 中搜索 `Imou Life` 并安装集成。</b> 在登录页填写 App ID、App secret，服务器区域选择 **中国**（须与在 open.imou.com 创建应用时使用的区域一致）。

<img src="assets/images/login_new.png" width="70%" alt="Imou Life 登录 — App ID、App Secret 与服务器区域">

然后选择要添加的设备。未勾选的设备仍留在 Home Assistant，但不会被轮询（可节省 API 配额）。

### 4. 完成

你的乐橙账号下的设备应已出现在 Home Assistant 中。

<img src="assets/images/integration_overview.png" width="70%" alt="Imou Life 集成条目与实体">

在集成条目上点 **配置**。菜单是 **轮询与摄像头**、**告警推送与通知**、**告警图片**、**告警时录像**、**选择要轮询的设备**、**绑定新设备**。每一页提交即保存并回到菜单；点 **完成** 关闭。

逐页说明，以及告警推送和告警图片都要用的 **设置 → 系统 → 网络**，还有 **告警时录像**：见 [配置项参考](guides/configuration.md#zh-hans)。

>说明：<br>
>本集成通过 Imou 开放平台进行云端远程访问。<br>
>云端 API 调用与视频播放会消耗 AppId 账号的资源配额 — 请在 [国内控制台](https://open.imou.com/consoleNew/resourceManage/myResource) 的 **我的资源** 中查看。

### 获取官方解密库

集成**不附带**告警图解密用的原生库。**现阶段请自行下载。** 仅 linux x86-64。

| 区域 | 下载地址 |
| --- | --- |
| 国内 | [openapi.lechange.cn … resourceType=ImageDecrpt](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt) |
| 海外 | [openapi.easy4ip.com … resourceType=HTTPInterfaceCallDemo](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo) |

打开地址后下载到的是压缩包。解压后，两个 `.so` 在 `Open-PicDecode/src/main/resources/linux-x86-64`。把 **`libLCOpenApiClient.so` 和 `libLCOpenSDK.so` 都** 复制到 `/config/imou_life/native/`，只放一个加载不起来。

## 功能

* **集成与账号**
  - 在 **配置 → 绑定新设备** 中将设备绑定到开放平台账号（设备序列号 + 绑定码）；账号下尚无设备时安装流程不再中止（可立即绑定或暂不选择设备完成配置）
  - 安装时及 **配置 → 选择要轮询的设备** 中可选择设备（仅轮询已选设备）
  - **配置** 菜单：**轮询与摄像头**、**告警推送与通知**、**告警图片**、**告警时录像**、**选择要轮询的设备**、**绑定新设备**；每一项可独立保存并回到菜单，菜单会显示当前哪些已开启
  - 登录界面与 Home Assistant Core 对齐：**服务器区域** 选择 **中国**
  - 界面支持英语、简体中文、德语、法语、意大利语（跟随 Home Assistant 语言设置）
  - 基于 [pyimouapi](https://pypi.org/project/pyimouapi/) 1.4.1 访问开放平台 API
* **事件推送与自动化**
  - 可选 Webhook 回调接收 Imou 云端实时消息（需公网可访问的 HA 地址或手动填写回调 URL）
  - **配置 → 告警推送与通知** — 回调地址（建议地址；不可达时改主机名和端口）、消息类型、手机通知。
  - Home Assistant 事件：`imou_life_event`（所有已接受推送）、`imou_life_alarm`（仅告警类）
  - 可选告警通知：在 **配置 → 告警推送与通知** 中选择 Companion App 等通知目标；某台不想推可在设备页关掉 **告警时通知**（默认开）。Companion App：点通知打开该设备在 Home Assistant 中的设备页
  - 可选 **在告警通知中显示图片**（默认关，**仅 linux x86-64**）：由 Home Assistant 下载密文并在本机解密。[自行下载官方库](#获取官方解密库)，再打开 **配置 → 告警图片**。许多移动侦测推送没有图片 URL，此时仍为纯文本。完整步骤：[配置项参考](guides/configuration.md#zh-hans)
  - 可选择推送消息类型——含物模型设备消息时，物模型开关 / 传感器 / 布防靠属性推送即时更新，不再跟间隔轮询走；消息也会同步到乐橙 App
  - **告警时录像** — 内置：每路镜头一个开关（默认关），保存目录和时长在 **配置 → 告警时录像**。不用写 YAML 自动化。完整步骤：[配置项参考](guides/configuration.md#record-on-alarm-zh)
* **摄像头**
  - 状态（名称、在线、存储、电量等）
  - 直播
  - 云台（方向按钮；时长在 **配置 → 轮询与摄像头 → 摄像头默认** 中设置）
  - **收藏点** — `select.collection_point`（**转到收藏点**）列出设备 / 乐橙 App 中的收藏点，选择后跳转（需 `CollectionPoint`；无法读取当前是否在某一收藏点）
  - 检测：画面变化、人形、宠物
  - **动态侦测**（`binary_sensor`，`device_class: motion`）— 仅在支持画面变化或人形检测的摄像头上出现。画面变化 / 人形 / PIR / 区域人形 AI / 越线 / 区域入侵推送后约亮 15 秒，PIR 清除立即关。宠物 / 车辆告警不驱动它。重启 Home Assistant 后复位为关。需 **启用事件推送** 且类型含 **alarm**；关掉推送时实体仍在，但是不可用。与 **画面变化** / **人形检测** 开关不同（开关是开不开检测，这个是刚才有没有检测到）
  - **门铃**（`event`，`device_class: doorbell`）— 仅在支持呼叫的摄像头上出现。事件推送里的按铃或来电会触发 `ring`。未接听不会。需 **启用事件推送** 且类型含 **alarm**；关掉推送时实体仍在，但是不可用
  - **告警图片**（`image`）— 该镜头最近一张已解密的告警图，可钉在仪表盘。需 **启用事件推送** 且类型含 **alarm**，并打开 **在告警通知中显示图片**；关掉其中任一项时实体仍在，但是不可用。要等一次带图且本机解密成功的推送才会有画面。不会去拍直播快照
  - 隐私模式、夜视、画面翻转、宽动态、智能追踪
  - 白光灯、告警联动白光灯、告警联动警笛（配置区开关）
  - 具备 Siren 能力或 IoT refs `25500`/`22200` 的设备提供 `siren` 实体（`siren.turn_on` / `siren.turn_off`）；手动开关**不依赖**事件推送。实体状态约 15 秒后自动复位（与固件常见鸣响时长一致）；若已开事件推送，收到 `sirenOff` 会立即关
  - 音频录制、设备提示音、异常音告警
  - 重启设备
* **告警传感器**
  - 状态（名称、在线、电量等）
  - 布防 — `alarm_control_panel`（在家 / 离家 / 撤防）
  - 告警音量
  - 一键消音
  - 指示灯
  - 温湿度
  - 重启设备
* **能源设备**
  - 状态（名称、用电、在线）
  - 插座开关与倒计时
  - 插座指示灯
  - 最大功率

## 故障排查

- **某个选项是干什么的 / 该怎么填？** — 见 [配置项参考](guides/configuration.md#zh-hans)，逐页说明每个选项，并给出告警推送和告警图片共同依赖的 **设置 → 系统 → 网络** 配置步骤。
- **告警图片开不了 / 提示缺少解密库** — 现阶段需自行下载官方 Demo 压缩包（[国内](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt) / [海外](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo)），解压后把 `Open-PicDecode/src/main/resources/linux-x86-64` 下的两个 `.so` 复制到 `/config/imou_life/native/`。见 [获取官方解密库](#获取官方解密库)。
- **App ID / App secret 无效** — Home Assistant 会打开**重新认证**流程；在 **设置 → 设备与服务 → Imou Life** 中输入新的 App secret（通知或三点菜单 → **重新认证**）。
- **事件推送不工作** — 打开 **配置 → 告警推送与通知**。确认 **启用事件推送** 已开启。**回调地址** 已预填生成的地址，若该地址公网不可达，就把主机名和端口改掉。同时检查 **设置 → 系统 → 网络 → Home Assistant URL**。在 **设置 → 系统 → 修复** 中查看 repair 提示。
  - 自动化可监听 `imou_life_event`（所有已接受推送）与 `imou_life_alarm`（仅安防告警）。隐私遮蔽消息（`openCamera` / `closeCamera`）仅触发 `imou_life_event`。
  - 若收到 `abAlarmSound` 或 `closeCamera`，说明回调/Webhook 路径正常。
  - 若始终收不到 `videoMotion` / `human` / `mobileDetect`：确认设备已开启画面变化/人形检测；下载**诊断**并查看 `event_push.recent_msg_type_counts`。缺少对应键表示云端/设备未推送该类型（非 HA 分类错误）。对不上 Home Assistant 设备的推送会被丢弃（仍返回 HTTP 200）。
  - 确认 **配置** 中已启用事件推送且推送类型包含 **alarm**。
  - **门铃** 没有实体：这台摄像头不支持呼叫。实体在但是不可用：打开事件推送且类型含 **alarm**。画面变化 / 人形是另一实体（**动态侦测**）。
  - **告警图片** 没有实体：那不是摄像头。实体在但是不可用：打开事件推送且类型含 **alarm**，并在 **配置 → 告警图片** 打开 **在告警通知中显示图片**。实体可用但没有画面：上次告警没有图，或解密没成功。该实体不会去拍直播快照。
  - **动态侦测** 没有实体：这台摄像头不支持画面变化或人形检测。实体在但是不可用：打开事件推送且类型含 **alarm**。实体可用但不亮：下载**诊断**查看 `event_push.recent_msg_type_counts`。较新的物模型云台常推 `e_multiVideoAiPerArea` / `e_smartMixDetect` / `e_areaDetect` / `crossLineDetection`，而不是 `human` / `videoMotion`——这些现在会驱动该实体。车辆 / 宠物仍不会。该实体不轮询云端。
  - 物模型开关 / 传感器重载后停住：确认已开事件推送且类型含 **物模型设备消息**；下载**诊断**看 `event_push.recent_msg_type_counts` 有没有 `iotProperty`。取消勾选该类型即恢复属性轮询。
- **升级后自动化** — v1.3.0 起移除自定义 `imou_life.turn_on` / `turn_off` / `select` 服务；请使用标准 `switch.turn_on`、`select.select_option`、`button.press`。v1.4.0 起 `select.*_mode` 的 `select.select_option` 改为 `alarm_control_panel.alarm_arm_home` / `alarm_arm_away` / `alarm_disarm`；`button.siren_start` / `siren_stop` 改为 `siren.turn_on` / `siren.turn_off`。遗留的 `select.*_mode` 和警号 button 注册行会在加载时删除。
- **云台收藏点** — 对 `select.<设备>_collection_point` 使用 `select.select_option`，选项填乐橙 App 中的收藏点名称。当前位置未知时，下拉框显示 **选择收藏点…**。
- **诊断** — 在集成条目的三点菜单中 **下载诊断**（已脱敏）。
- **告警时录像没有文件** — 确认已开事件推送、该路镜头开关，以及保存目录已加入白名单：见 [配置项参考](guides/configuration.md#record-on-alarm-zh)。

## 贡献

欢迎贡献代码。提交 Pull Request 前请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- 开发环境：`script/setup`
- 代码检查：`script/lint-check`
- 测试：`script/test`

## 统计

| 指标 | 来源 | 说明 |
| --- | --- | --- |
| **活跃安装量** | [Home Assistant Analytics](https://analytics.home-assistant.io/custom_integrations.json) | 上报 `imou_life` 的 HA 实例（仅参与统计的实例） |
| **HACS 下载量** | GitHub Releases | `imou_life.zip` 累计下载（HACS 安装与更新） |

<!-- Badge references -->
[hacs-badge]: https://img.shields.io/badge/HACS-Default-orange.svg?logo=HomeAssistantCommunityStore&logoColor=white&style=flat-square
[release-badge]: https://img.shields.io/github/v/release/Imou-OpenPlatform/Imou-Home-Assistant?style=flat-square&label=Release
[downloads-badge]: https://img.shields.io/github/downloads/Imou-OpenPlatform/Imou-Home-Assistant/total.svg?style=flat-square&label=HACS%20downloads
[installs-badge]: https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=Active%20installs&suffix=%20installs&cacheSeconds=21600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.imou_life.total
[hacs-url]: https://github.com/hacs/integration
[release-url]: https://github.com/Imou-OpenPlatform/Imou-Home-Assistant/releases
[analytics-url]: https://analytics.home-assistant.io/custom_integrations.json

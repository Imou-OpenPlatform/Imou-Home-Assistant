# Configure reference / 配置项参考

**[English](#english)** | **[简体中文](#zh-hans)**

---

<a id="english"></a>

## English

Everything on this page lives under **Settings → Devices & services → Imou Life → Configure**.

Each page saves when you submit it and returns to the menu, so changing two things does not mean opening **Configure** twice. **Done** closes the dialog. The line at the top of the menu reports what is currently on, so no page has to be opened just to check.

### Step 0 — Set the Home Assistant URL first

Two features depend on this one Home Assistant setting, and without it both fail quietly:

| Feature | Who connects to whom | Needs |
| --- | --- | --- |
| Alarm push | Imou cloud → your Home Assistant | An address reachable from the internet |
| Alarm pictures in phone notifications | Your phone → your Home Assistant | An address reachable from wherever the phone is |

The phone and the Imou cloud both fetch these addresses themselves, so a LAN address such as `http://192.168.1.5:8123` can never work from outside your home.

1. Open **Settings → System → Network**.
2. Under **Home Assistant URL → Internet**, enter the address that reaches this instance from outside, including scheme and port — for example `https://ha.example.com` or `https://ha.example.com:8123`. No trailing slash and no path. If you use Home Assistant Cloud (Nabu Casa), let the cloud option provide it instead.
3. **Local network** can stay on automatic detection.
4. Save.
5. Check it from the phone: turn WiFi off, open that address in the phone browser. The Home Assistant login page means it works.
6. Reopen **Configure → Alarm push and notifications**. **Callback URL** is prefilled from this setting. If the page opens with a bold warning that the address is only reachable on your local network, step 2 has not taken effect.

Two things worth knowing:

- **Self-signed certificates.** A phone that does not trust the certificate can refuse to download the alarm picture even though the notification itself arrives. Use a certificate the phone trusts.
- **Reverse proxy on a different hostname, port, or path.** Leave the network setting alone and type the address your proxy exposes into **Callback URL**; that field always wins over the generated one.

### Polling and cameras

| Option | What it does | Default |
| --- | --- | --- |
| Enable status polling | Periodically refresh device state from the cloud. Off means entity states can go stale; setup and reload still fetch the device list once. | On |
| Polling interval | Seconds between refreshes, 30–900. Ignored while polling is off. | 300 |
| Snapshot wait time | How long to wait after asking a camera for a snapshot, since the camera has to take and upload it first. Raise it if snapshots come back stale or empty. Range 1–9. | 3 |
| Video resolution | `HD` or `SD`, used by the Live camera entity. | HD |
| PTZ rotation duration | How far one press of a direction button moves, expressed as movement time in milliseconds. Range 100–10000. | 500 |

Live streams always use `https`. There is no protocol choice: on an HTTPS Home Assistant a plain `http` stream URL is blocked by browsers as mixed content.

### Alarm push and notifications

| Option | What it does | Default |
| --- | --- | --- |
| Enable event push | Registers this Home Assistant's webhook with the Imou cloud. Everything alarm-driven — notifications, alarm pictures, record on alarm — runs off this. Alarms still reach the Imou Life app either way. | Off |
| Callback URL | Where the Imou cloud POSTs alarms. Prefilled from **Step 0**; must be reachable from the internet. Only change the hostname and port unless a proxy also rewrites the path. | generated |
| Subscribe to | Which cloud message types to receive. | Device alarms, online/offline, IoT messages |
| Notification targets | Registered notify services to message on an alarm, one message per alarm. Leave empty to drive everything from automations instead. Add `notify.persistent_notification` to also get the alarm in the Home Assistant web notification drawer. | empty |

A device page also has a **Notify on alarm** switch, so one noisy camera can be silenced without touching this page.

### Alarm pictures

Alarm pictures arrive encrypted. Home Assistant downloads the ciphertext itself and decrypts it locally, so no Open API quota is spent.

| Option | What it does | Default |
| --- | --- | --- |
| Show the picture in alarm notifications | Decrypt the alarm picture and put it in notifications. Cannot be switched on while the native libraries are missing. | Off |
| Default device password | Fallback for devices that do not have their own password. Saved values are filled back in (masked). | empty |
| Per-device password | One field per device that needs a password, labelled with the device name and serial. Saved values are filled back in (masked). | empty |
| Remove stored passwords | Deletes the passwords of the serials you check. | — |

Prerequisites:

- **linux x86-64 only.** The integration does not ship the native libraries; download the official Demo package yourself. China: [ImageDecrpt](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt). Overseas: [HTTPInterfaceCallDemo](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo). The download is a zip; after extracting, copy both `libLCOpenApiClient.so` and `libLCOpenSDK.so` from `Open-PicDecode/src/main/resources/linux-x86-64` into `/config/imou_life/native/`. `libLCOpenSDK.so` does the decrypting and `libLCOpenApiClient.so` supplies the OpenSSL symbols it links against, so one alone will not load. The page reports whether they were found.
- **A password, but only for some devices.** The page lists exactly the devices that need one; anything not listed derives its key from its serial number and needs nothing from you.
- **An internet-reachable Home Assistant URL** (Step 0), or phones off the LAN cannot load the picture.

Pictures are written to `/local/imou_life/thumbs/` and served **without authentication** for roughly 24 hours, so anyone holding the URL can view them. If `/config/www` did not exist before, restart Home Assistant once after the first picture is written. Many motion pushes carry no picture URL at all; those notifications stay text-only.

On iOS an unexpanded notification shows a small thumbnail — long-press or pull it down to see the full-size picture. Alarm stills come from the cloud at the resolution it chose, commonly 640×480.

<a id="record-on-alarm"></a>

### Record on alarm

When an Imou **alarm** is pushed, cameras whose **Record on alarm** switch is on save a short MP4 from the **cloud HLS live stream**. The integration does this itself — you do not write an automation. This is post-event recording, not an NVR. Dual-lens devices have one switch per channel. Each clip consumes **Open Platform live-view quota**.

| Option | What it does | Default |
| --- | --- | --- |
| Save folder | Where clips are written, for example `/media/imou`. Must be listed in `allowlist_external_dirs` (creating the folder alone is not enough). Empty means no recording. | empty |
| Clip duration | Seconds recorded after an alarm, 15–180. Uses live-stream quota. | 60 |

These two fields apply to every camera on this account and do not turn recording on by themselves. On each camera device page, turn on **Record on alarm** (configuration section) for the lenses you want. Default is off so one alarm does not start pulling live streams for every device.

On Home Assistant OS, `/media/imou` is typical. Merge this into your existing `homeassistant:` block, then restart. Core / development installs should use an absolute path under the config folder instead.

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

After a real alarm, a file like `/media/imou/<deviceId>_<channel>_<timestamp>.mp4` should appear. Wait until the camera entity is not `unavailable`.

Not supported: reliable pre-roll, 24/7 NVR-style recording, writing the switch back to the Imou cloud, downloading Imou **cloud** history clips, or local RTSP (live view is cloud HLS). Overlapping alarms on the same camera are skipped until the current clip duration elapses.

| Symptom | What to do |
| --- | --- |
| Switch on but no file | Confirm this page has a folder; confirm event push and **alarm** type; confirm the camera switch is on. |
| Options save refused / path not allowed | Folder is not under `allowlist_external_dirs`, directory missing, or absolute path mismatch. Fix and restart. |
| Camera unavailable | Wait until the camera state is not `unavailable` after restart. |
| Empty / failed MP4 | Stream URL expired, network issue, or quota; retry; check logs around `getLiveStreamInfo` / stream / ffmpeg. On Home Assistant Core, if logs say Stream is not set up, add `stream:` to `configuration.yaml` and restart. |

### Devices

| Page | What it does |
| --- | --- |
| Choose devices to poll | Unselected devices stay in Home Assistant but are not refreshed. Useful for trimming cloud calls on a large account. |
| Bind a new device | Adds a device by serial number, plus a binding code if the device requires one. |

### What depends on what

| To get this | You need |
| --- | --- |
| Any alarm notification, picture, recording, or your own automation | **Enable event push** on, with a Callback URL the Imou cloud can reach |
| A picture in a phone notification | Event push, **Show the picture in alarm notifications**, both `.so` files, a password for the devices the page lists, and an internet-reachable Home Assistant URL |
| A picture in the web notification drawer | The same, plus `notify.persistent_notification` among the notification targets |
| A clip after an alarm | Event push, an allowlisted save folder, and the per-camera switch on |

### Related

- [README](../README.md#english) — installation and features

---

<a id="zh-hans"></a>

## 简体中文

本页所有内容都在 **设置 → 设备与服务 → Imou Life → 配置** 下。

每一页提交后即保存并回到菜单，要改两处设置不用进两次 **配置**；关闭由 **完成** 这一项负责。菜单顶部那行会报告当前哪些功能是开着的，不必逐页点进去看。

### 第 0 步 —— 先配好 Home Assistant 地址

有两个功能依赖 Home Assistant 的这一个设置，而不配的话两个都会静默失效：

| 功能 | 谁访问谁 | 需要 |
| --- | --- | --- |
| 告警推送 | Imou 云 → 你的 Home Assistant | 一个公网可达的地址 |
| 手机通知里的告警图片 | 你的手机 → 你的 Home Assistant | 一个在手机所处网络下可达的地址 |

手机和 Imou 云都是**自己去访问**这个地址的，所以像 `http://192.168.1.5:8123` 这种局域网地址，出了家门就永远访问不到。

1. 打开 **设置 → 系统 → 网络**。
2. 在 **Home Assistant URL → 互联网** 里填从外部能访问到本实例的地址，**要带协议和端口**，例如 `https://ha.example.com` 或 `https://ha.example.com:8123`。末尾不要加斜杠，也不要带路径。如果你用 Home Assistant Cloud（Nabu Casa），交给云端选项提供即可。
3. **本地网络** 保持自动探测就行。
4. 保存。
5. 用手机验证：关掉 WiFi，在手机浏览器里打开这个地址。能看到 Home Assistant 登录页就说明通了。
6. 回到 **配置 → 告警推送与通知**。**回调地址** 会用这个设置预填。如果这一页打开时顶部有一条加粗提示说该地址只能在局域网访问，说明第 2 步没生效。

两点值得注意：

- **自签证书。** 手机不信任证书时，通知本身能到，但图片可能拒绝下载。请使用手机信任的证书。
- **反向代理的主机名、端口或路径不同。** 网络设置照原样放着，把代理对外暴露的地址直接填进 **回调地址**，这个字段永远优先于自动生成的地址。

### 轮询与摄像头

| 选项 | 作用 | 默认 |
| --- | --- | --- |
| 启用状态轮询 | 定期从云端刷新设备状态。关闭后实体状态可能过期；但初始化和重新加载仍会拉取一次设备列表。 | 开 |
| 轮询间隔 | 两次刷新之间的秒数，30–900。轮询关闭时此项无效。 | 300 |
| 抓图等待时间 | 向摄像头请求抓图后等待多久再去取——因为摄像头要先拍好并上传。取回的图偏旧或为空时调大。范围 1–9。 | 3 |
| 视频分辨率 | `HD` 或 `SD`，用于「直播」摄像头实体。 | HD |
| 云台转动时长 | 方向键按一次转多远，以移动时间（毫秒）表示。范围 100–10000。 | 500 |

直播固定使用 `https`，不提供协议选择：HA 本身跑在 HTTPS 上时，`http` 的流地址会被浏览器按混合内容拦掉。

### 告警推送与通知

| 选项 | 作用 | 默认 |
| --- | --- | --- |
| 启用事件推送 | 把本实例的 Webhook 注册到 Imou 云。所有由告警驱动的功能——通知、图片解密、告警时录像——都跑在这条链路上。无论开关如何，告警都会同步到乐橙 App。 | 关 |
| 回调地址 | Imou 云向哪个地址推送告警。由 **第 0 步** 预填，必须公网可达。除非代理也改写了路径，否则只改主机名和端口。 | 自动生成 |
| 订阅类型 | 要接收哪些云端消息类型。 | 设备告警、上下线、IoT 消息 |
| 通知目标 | 告警时要通知的 notify 服务，每条告警发一条消息。留空则完全交给自动化。想在 Home Assistant 网页通知栏也看到告警，把 `notify.persistent_notification` 加进来。 | 空 |

设备页上还有一个 **告警时通知** 开关，可以单独静音某个吵闹的摄像头，不用动这一页。

### 告警图片

告警图片是加密下发的。Home Assistant 自己下载密文再在本机解密，不消耗开放平台配额。

| 选项 | 作用 | 默认 |
| --- | --- | --- |
| 在告警通知中显示图片 | 解密告警图片并放进通知。本机缺少原生库时不允许打开。 | 关 |
| 默认设备密码 | 没有单独填写密码的设备用这个。已存密码会回填（掩码显示）。 | 空 |
| 各设备密码 | 每个需要密码的设备一个输入框，标签是设备名和序列号。已存密码会回填（掩码显示）。 | 空 |
| 删除已存密码 | 删除你勾选的那些序列号的密码。 | — |

前置条件：

- **仅 linux x86-64。** 集成不附带原生库，现阶段请自行下载官方 Demo 包。国内：[ImageDecrpt](https://openapi.lechange.cn/openweb/getPublicResourceUrl?resourceType=ImageDecrpt)。海外：[HTTPInterfaceCallDemo](https://openapi.easy4ip.com/openweb/getPublicResourceUrl?resourceType=HTTPInterfaceCallDemo)。下载到的是压缩包，解压后从 `Open-PicDecode/src/main/resources/linux-x86-64` 把 `libLCOpenApiClient.so` 和 `libLCOpenSDK.so` 都复制到 `/config/imou_life/native/`。`libLCOpenSDK.so` 负责解密，`libLCOpenApiClient.so` 提供它链接的 OpenSSL 符号，只放一个加载不起来。这一页会报告是否找到。
- **只有部分设备需要密码。** 页面上列出来的就是需要密码的那些；没有列出的设备用自己的序列号推导密钥，不需要你填任何东西。
- **一个公网可达的 Home Assistant 地址**（第 0 步），否则不在局域网的手机加载不出图片。

图片写在 `/local/imou_life/thumbs/`，**不做身份验证**，保留约 24 小时，也就是说拿到 URL 的人都能查看。如果 `/config/www` 原先不存在，首次生成图片后需要重启一次 Home Assistant。很多移动侦测推送根本不带图片 URL，这类通知仍是纯文本。

iOS 上未展开的通知只显示小缩略图，**长按或下拉展开才是原图**。告警图由云端按它选定的清晰度下发，常见是 640×480。

<a id="record-on-alarm-zh"></a>

### 告警时录像

乐橙**告警**推送到 Home Assistant 后，打开了 **告警时录像** 开关的摄像头会从**云端 HLS 直播流**保存一段短 MP4。集成自己完成，不用写自动化。这是事后短视频，不是 NVR。双目设备按通道各有一个开关。每次录制消耗开放平台**直播配额**。

| 选项 | 作用 | 默认 |
| --- | --- | --- |
| 保存目录 | 片段写到哪里，例如 `/media/imou`。必须在 `allowlist_external_dirs` 中（只建文件夹不够）。留空表示不录。 | 空 |
| 片段时长 | 告警后录制的秒数，15–180。会占用直播配额。 | 60 |

这两项对账号下所有摄像头共用，本身不会打开录像。在各摄像头设备页打开 **告警时录像**（配置区），只给需要的镜头打开。默认关闭，避免一次告警把账号下所有设备都拉去直播。

Home Assistant OS 上常用 `/media/imou`。把下面这段**合并**进已有的 `homeassistant:`，然后重启。Core / 开发环境请用配置目录下的绝对路径。

```yaml
homeassistant:
  allowlist_external_dirs:
    - /media/imou
```

真实告警之后应出现类似 `/media/imou/<deviceId>_<channel>_<时间戳>.mp4` 的文件。等相机实体不是 `unavailable` 再测。

不支持：可靠预录、7×24 NVR 式录像、把开关写回乐橙云、下载乐橙**云端历史**录像、局域网 RTSP（当前直播为云端 HLS）。同一摄像头在当前片段时长内的重复告警会被跳过。

| 现象 | 处理 |
| --- | --- |
| 开关已开但没有文件 | 确认本页已填目录；确认已启用事件推送且包含 **alarm**；确认该路镜头开关已开。 |
| 选项保存被拒 / 路径不在白名单 | 目录不在 `allowlist_external_dirs`、文件夹不存在、或绝对路径不一致。修正后重启。 |
| 相机不可用 | 重启后等待相机状态非 `unavailable`。 |
| MP4 为空或失败 | 流地址过期、网络或配额问题；重试；查看 `getLiveStreamInfo` / stream / ffmpeg 相关日志。Home Assistant Core 若日志说 Stream 未启用，在 `configuration.yaml` 增加 `stream:` 后重启。 |

### 设备

| 页面 | 作用 |
| --- | --- |
| 选择要轮询的设备 | 未勾选的设备仍留在 Home Assistant，但不再刷新。设备多的账号可以用它减少云端调用。 |
| 绑定新设备 | 用序列号添加设备，设备需要时再填绑定码。 |

### 依赖关系一览

| 想要 | 需要 |
| --- | --- |
| 告警通知、图片、录像，或你自己写的自动化 | 开启 **启用事件推送**，且回调地址 Imou 云能访问到 |
| 手机通知里带图 | 事件推送、**在告警通知中显示图片**、两个 `.so` 文件、页面上列出的那些设备的密码，以及一个公网可达的 Home Assistant 地址 |
| 网页通知栏里带图 | 同上，另外把 `notify.persistent_notification` 加进通知目标 |
| 告警后有录像片段 | 事件推送、一个已加入白名单的保存目录，以及该摄像头的开关已打开 |

### 相关文档

- [README](../README.md#zh-hans) —— 安装与功能

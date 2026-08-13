# CHANGELOG

## English

### [1.3.4.1]

#### Fixed

- Event-push webhook accepts GET probes from Imou cloud when enabling the callback. Home Assistant allows only POST and PUT by default and answers a GET on a registered webhook with `405`, which made `setMessageCallback` fail with `OP1003` (#88)

### [1.3.4]

#### Breaking

- Home Assistant 2025.4 or newer is required. Cores below that no longer see this integration in HACS
- Select option states use friendly keys (`home`/`away`/`disarm`, `mute`/`low`/`medium`/`high`, night-vision string keys) matching pyimouapi 1.3.4. Automations calling `select.select_option` with the old numeric values (`"0"`, `"1"`, …) must be updated

#### Added

- Bind devices to the open-platform account from Configure → Manage devices (serial + verification code); setup no longer aborts when the account has zero devices (bind now or finish with an empty selection)
- **Enable status polling** option in Configure → General; disable to stop background status refreshes and save Open API quota (#15)

#### Changed

- Default status polling interval is 300 seconds (5 minutes), was 60 seconds. Existing entries keep the interval already saved in options
- Options flow: top-level menu for General, Event push, and Manage devices; each section saves on Submit without changing the other sections. Bind a new device lives under Manage devices
- Config / options flows surface Imou Open API `code`/`msg` in the UI (e.g. `OP1013` quota exceeded) instead of a generic “request failed” (#67)
- All entity writes (switch, select, button, text) use optimistic local updates and no longer trigger an immediate full cloud poll
- Errors raised while operating a device are translated, so the UI shows them in your language instead of the raw English message from the API
- Listing the account now runs on its own ten minute clock rather than on every status poll. Status still refreshes at the interval you configured; only the check for devices added to or removed from the account slowed down, which is where most of the Open API quota was going. A device added in the Imou app appears within ten minutes
- Settings that configure a device (detection switches, volume, night vision, thresholds, timers, restart) are filed under the device's configuration section instead of sitting among its primary controls. Entities in that section are hidden from auto-generated dashboards and are not exposed to Assist by default; the entities themselves are unchanged, so anything referring to them by entity id keeps working
- Invalid credentials now end the polling and ask you to sign in again, rather than retrying a secret the integration already knows is refused
- Setting up an account that holds no devices stores an empty device list. A device bound later from the Imou app is not polled until you select it under Configure → Manage devices — same as when the account already had cameras at setup
- Depend on `pyimouapi==1.3.5`, which brings concurrent status reads, a per-host connection cap so snapshot downloads cannot stall status polling, credentials kept out of debug logs, and several connection-leak and paging fixes
- A camera that cannot produce a snapshot reports why, in your language, instead of showing a blank tile
- One unreadable accessory no longer leaves the whole account showing as unavailable
- Less log noise: the device filter, and devices that are asleep, are logged at debug rather than info
- Issue templates: expand Feature request and Question forms; use `feature` label aligned with `[Feature]` titles

#### Fixed

- On IPC-K7C (product_id `FKX9UYL4`), `motion_detect` skips advertised but unusable refs `14800` and `305000` and binds `108800`, so `switch.turn_on` / `turn_off` no longer returns `40999` (#77). The switch remains
- Saving options with **Continue without binding** when the account has no devices replaces the old device selection with an empty list (including one stored from setup in entry data). Previously the previous ids were written back, so devices deleted in the Imou app kept showing after save
- Submitting **General** or **Event push** does not write a device whitelist. A list is stored only when you submit **Manage devices** (or bind there). The old options wizard always ended on the device list, which could snapshot the account and filter out a camera bound later from the Imou app
- After a reload, the first account listing detaches Home Assistant devices that are no longer on the account. Unload leaves registry entries alone by design; the next setup used to skip cleanup when starting from an empty map, so deleted devices stayed visible with their last status. Deselecting a device in options only stops polling — it does not remove the Home Assistant device
- Refusing to delete a multi-channel camera/NVR channel raises a clear error in the UI instead of a silent rejection that the frontend showed as `[object Object]`. Devices already gone from the account can be removed from Home Assistant again
- Device **Download diagnostics** now includes that device's ids, model, status, and entity summaries (secrets still redacted). Previously only account-level diagnostics existed, so the device-page download had almost nothing useful for a single camera
- The `webhook` component is declared in the manifest. Event push registers a webhook and the config flow generates its URL, so on an installation that did not already load `webhook` for another reason this could fail
- The API session and the webhook registration are released when setup fails or is retried, instead of leaking across the retry
- `control_move_ptz` accepts a target entity and its `duration` limit matches what the service actually allows
- A rotated or revoked App Secret is noticed on the next status poll and opens the re-authentication prompt. Because listing the account moved to a ten minute clock, and because the library logged credential errors rather than reporting them, this could otherwise go unnoticed for ten minutes — or indefinitely with status polling turned off — while every entity kept showing its last known value as current
- Turning event push off in the options now tells the Imou cloud to stop pushing. The new setting was already saved by the time the integration reloaded, so it concluded push had never been on and left the cloud callback registered, spending Open API quota on messages that were then discarded
- The options can be saved when the device list cannot be fetched. Listing the account was the last step and had no way past it, so a quota-exceeded or unreachable account discarded every change — including the polling interval and event push settings you would want to change in exactly that situation
- A device removed from the account no longer breaks the update for the remaining entities. Its select and text entities raised while Home Assistant collected their attributes, which happens before availability is checked
- Re-authenticating successfully shows a confirmation instead of a blank message
- Deleting one camera of an NVR, or one lens of a multi-lens camera, no longer removes the others. Those arrive from the account as one device carrying several channels, and each channel becomes its own device here; the exclusion recorded on deletion is per account device, so it took the siblings with it along with any names, areas, and automations attached to them. Deleting a single channel is now refused with an explanation in the log — deselect the device in the options to stop polling it, or disable the channel's entities to hide it

### [1.3.3]

#### Added

- `select.collection_point` for PTZ preset query and goto (#53, #71) — lists presets from the cloud/device; placeholder **Select a preset…** / **选择收藏点…** when current position is unknown; use standard `select.select_option` in automations
- `button.siren_start` and `button.siren_stop` for devices with Siren capability or IoT refs `25500`/`22200`

#### Changed

- Depend on `pyimouapi==1.3.3`
- Select and switch commands no longer trigger an immediate full cloud poll; UI updates from optimistic local state (saves API quota)

### [1.3.2]

#### Changed

- Options flow: split Configure into General, Event push, and Devices steps; group event push settings (callback URL, message types, notifications)
- Config flow login step aligned with Core imou (region dropdown, shared error keys)
- Sensor, select, and switch platforms use EntityDescription whitelists aligned with Core

### [1.3.1]

#### Changed

- Event push always syncs to the Imou app (`basePush=1`); removed the Base push option from Configure
- Webhook `msg_type` uses top-level `msgType` only; still expose `product_id` (`pid`) and `outputData`; treat `iotEvent` / `sirenOn` / `sirenOff` as alarms
- Webhook: resolve numeric / `iotEvent` push types to product-model event identifiers via pyimouapi 1.3.2 (alarm classification still uses top-level `msgType`)
- Depend on `pyimouapi==1.3.2`

#### Fixed

- Webhook: treat privacy-mask and other status/ops msgTypes (`openCamera`, `closeCamera`, `electricity`, …) as non-alarm (`imou_life_event` only); expose recent push msgType counts in diagnostics (#66)
- Preserve empty `selected_devices` (do not treat `[]` as unset); close Open API client on unload; persist device removal into `selected_devices` so poll does not re-add it
- Webhook notify/events prefer HA device registry name (`device_name`) over push `cname`/`dname`
- Webhook ACKs HTTP 200 before identifier resolve/notify; refuse device removal when the coordinator map cannot safely materialize an allow-list

### [1.3.0]

#### Added

- Reauth flow when App Secret expires
- Repair issues for event push URL and callback registration failures
- Config entry diagnostics (redacted secrets)
- `integration_type: hub` and `quality_scale.yaml`

#### Changed

- Webhook runtime data isolated per config entry; config entry v2 migration adds missing `webhook_id`
- Config entry titles show readable integration name; abort when device list is empty or unavailable
- Switch/select/text writes trigger coordinator refresh; switch types whitelisted
- All platforms declare `PARALLEL_UPDATES = 0`

#### Fixed

- Unload no longer bulk-removes device registry entries
- Removed unreliable camera `is_recording` / `is_streaming` properties
- `async_get_cached_translations()` signature compatibility (from v1.2.9 follow-up)

#### Breaking

- Removed custom entity services `imou_life.turn_on`, `turn_off`, `select`, and `restart_device`. Use standard `switch.turn_on`, `select.select_option`, and `button.press` instead.

### [1.2.9]

#### Added

- pyimouapi 1.2.9 dependency for API encapsulation (no direct OpenAPI paths in integration code)
- Full i18n: no Chinese in Python; translations for webhook messages and config flow strings
- Coordinator `devices_by_key` and dynamic device hot-load when device lists change
- `ImouRuntimeData` replaces `hass.data` for event push wiring

#### Changed

- Refactor aligned with Home Assistant best practices; no breaking changes for existing users
- README features section updated (device selection, event push, translations)

### [1.2.10]

#### Added

- Add setup/options device selection so users can choose which Imou devices to include
- Add webhook alarm push support with Home Assistant events and notifications
- Add tests for device selection and webhook edge cases

#### Changed

- Treat non-alarm push types such as iotProperty as generic events to avoid alarm notification spam

### [1.2.8]

#### Changed

- Bump pyimouapi to 1.2.8 (batch property polling via getIotDeviceDetailInfo)

### [1.2.7]

#### Added

- Contributor governance: PR template, CI (lint/test/hassfest/HACS), CODEOWNERS, and CONTRIBUTING guide
- Issue automation: stale label and auto-close after maintainer reply; simplified new-issue auto-reply

#### Changed

- Bump pyimouapi to 1.2.7
- Default device polling interval changed from 60s to 120s
- GitHub Actions dependencies updated

### [1.2.0]

#### Added

- Support the access of smart sockets

#### Changed

- Fixed some bugs
- Optimize the operation logic of IoT devices

### [1.1.0]

#### Added

- Support for Imou home security device integration
- Support for multiple lens camera integration
- Support for integrated option configuration
- Support for binary sensor entity type
- Support for Chinese translation of entity status

#### Changed

- Fixed some bugs
- Optimized interaction logic with the platform, offline devices will no longer request updates
- Other code optimizations

### [1.0.1]

#### Added

- Test case
- Github action
- Pre-commit hook

#### Changed

- Code optimization

### [1.0.0]

#### Added

- First release

---

## 中文

### [1.3.4.1]

#### 修复

- 事件推送 webhook 接受乐橙云打开回调时的 GET 探测。Home Assistant 默认只允许 POST 与 PUT，对已注册 webhook 的 GET 返回 `405`，导致 `setMessageCallback` 报 `OP1003` 失败（#88）

### [1.3.4]

#### 破坏性变更

- 需要 Home Assistant 2025.4 或更新版本。更低版本的 Core 在 HACS 中将看不到本集成
- Select 选项状态改为友好键（`home`/`away`/`disarm`、`mute`/`low`/`medium`/`high`、夜视字符串键），与 pyimouapi 1.3.4 一致。仍用旧数字值（`"0"`、`"1"`…）调用 `select.select_option` 的自动化需更新

#### 新增

- 可在「配置 → 管理设备」用序列号 + 验证码将设备绑定到开放平台账号；账号暂无设备时安装流程不再中止（可立即绑定或暂不选择设备完成配置）
- 「配置 → 常规」增加 **启用状态轮询**；关闭后停止后台状态刷新以节省 Open API 配额（#15）

#### 变更

- 状态轮询默认间隔改为 300 秒（5 分钟），原先为 60 秒。已保存过间隔的条目仍用原来的值
- 选项流程：顶层菜单分为常规、事件推送、管理设备；每一项点提交即保存且不影响其他分区。绑定新设备放在管理设备页内
- 配置/选项流程在界面展示开放平台 `code`/`msg`（如 `OP1013` 配额超限），而不再只显示笼统的「请求失败」（#67）
- 所有实体写入（switch、select、button、text）改为乐观本地更新，不再立即触发整次云端轮询
- 操作设备时的错误会走翻译，界面按你的语言显示，而不再是 API 原始英文
- 账号设备列表改为约十分钟一次，而不再每次状态轮询都拉。状态仍按你配置的间隔刷新；仅「账号增删设备」的检查变慢（原先最耗配额）。在 App 新加的设备约十分钟内会出现
- 配置类设置（侦测开关、音量、夜视、阈值、定时、重启）归入设备「配置」分区，而不再与主控件混在一起。该分区实体默认不出现在自动生成仪表盘、也不暴露给 Assist；实体本身未改，按 entity id 引用的自动化仍可用
- 凭证无效时会结束轮询并要求重新登录，而不再反复重试已知被拒绝的密钥
- 账号暂无设备时安装会写入空的设备列表。之后在 App 绑定的设备不会自动轮询，需到「配置 → 管理设备」勾选——与安装时账号里已有摄像头的路径一致
- 依赖 `pyimouapi==1.3.5`：并发状态读取、按主机连接上限（抓图不会拖死状态轮询）、debug 日志不含凭证，以及多处连接泄漏与分页修复
- 无法抓图的摄像头会用你的语言说明原因，而不再只显示空白图块
- 单个配件读失败不再导致整账号显示不可用
- 减少日志噪音：设备过滤与休眠设备改为 debug 而非 info
- Issue 模板：扩展功能请求与提问表单；`feature` 标签与 `[Feature]` 标题对齐

#### 修复

- IPC-K7C（产品 `FKX9UYL4`）的 `motion_detect` 会跳过宣称但不可用的 refs `14800`、`305000`，改绑 `108800`，开关 `turn_on`/`turn_off` 不再返回 `40999`（#77）。开关本身还在
- 账号无设备时用 **不绑定并保存** 会把旧的设备选择换成空列表（含初次配置写在 entry data 中的名单）。原先会写回旧 id，App 已删设备保存后仍显示
- 提交 **常规** 或 **事件推送** 不会写入设备白名单。只有在 **管理设备** 提交（或在该页绑定）时才保存名单。旧版选项向导总是以设备列表收尾，可能把当时账号快照写成过滤，之后在 App 绑定的设备会被滤掉
- 重新加载后，首次拉取账号列表会卸掉账号上已不存在的 Home Assistant 设备。卸载故意保留注册表条目；下次 setup 若从空 map 起步会跳过清理，已删设备仍以最后状态显示。在选项中取消勾选只停止轮询，不会从 Home Assistant 设备注册表移除该设备
- 拒绝删除多通道/NVR 某一通道时，在界面抛出可读错误，而不再是前端显示成 `[object Object]` 的静默拒绝。账号上已不存在的设备也可以再次从 Home Assistant 中删除
- 设备页 **下载诊断信息** 现包含该设备的 ID、型号、状态与实体摘要（密钥仍脱敏）。原先只有账号级诊断，设备页下载对单台相机几乎没用
- manifest 声明 `webhook` 组件。事件推送会注册 webhook 且配置流程会生成 URL；若安装尚未因其他原因加载 `webhook`，此前可能失败
- setup 失败或重试时会释放 API session 与 webhook 注册，而不再泄漏到下一次重试
- `control_move_ptz` 接受目标实体，且 `duration` 上限与服务实际允许范围一致
- 轮换或吊销的 App Secret 会在下一次状态轮询被发现并弹出重新认证。因账号列举改为约十分钟一次，且库原先只记日志不上报，否则可能十分钟内（关闭状态轮询时甚至一直）察觉不到，实体仍把最后已知值当成当前
- 在选项中关闭事件推送会通知乐橙云停止推送。原先设置已在 reload 前保存，集成误以为推送从未开启，云端回调仍注册，浪费配额接收随后被丢弃的消息
- 无法拉取设备列表时仍可保存其他选项。列举账号曾是最后一步且无法跳过，配额超限或账号不可达时会丢掉全部更改——包括此时最该改的轮询间隔与事件推送
- 账号移除设备不再打断其余实体的更新。其 select/text 在 HA 收集属性时抛错（早于可用性检查）
- 重新认证成功会显示确认，而不再是空白提示
- 删除 NVR 的某一路摄像头或多镜头相机的某一镜头，不再带走其余通道。账号侧是一台多通道设备，此处每通道各自成设备；删除时按账号设备记排除，会连同兄弟通道及其名称、区域、自动化一起去掉。现拒绝删除单通道并在日志说明——要停止轮询请在选项中取消勾选整机，要隐藏该通道请禁用其实体

### [1.3.3]

#### 新增

- `select.collection_point` 用于云台预置位查询与调用（#53、#71）— 从云端/设备列出预置位；当前位置未知时占位为 **Select a preset…** / **选择收藏点…**；自动化使用标准 `select.select_option`
- 具备 Siren 能力或 IoT refs `25500`/`22200` 的设备提供 `button.siren_start` 与 `button.siren_stop`

#### 变更

- 依赖 `pyimouapi==1.3.3`
- Select/switch 指令不再立即触发整次云端轮询；界面靠乐观本地状态更新（节省 API 配额）

### [1.3.2]

#### 变更

- 选项流程：配置拆为常规、事件推送、设备三步；事件推送设置分组（回调 URL、消息类型、通知）
- 配置流程登录步与 Core imou 对齐（区域下拉、共用错误键）
- Sensor、select、switch 平台使用与 Core 对齐的 EntityDescription 白名单

### [1.3.1]

#### 变更

- 事件推送始终同步到乐橙 App（`basePush=1`）；配置中移除「基础推送」选项
- Webhook `msg_type` 仅用顶层 `msgType`；仍暴露 `product_id`（`pid`）与 `outputData`；`iotEvent`/`sirenOn`/`sirenOff` 视为告警
- Webhook：经 pyimouapi 1.3.2 将数字/`iotEvent` 推送类型解析为产品型号事件标识（告警分类仍用顶层 `msgType`）
- 依赖 `pyimouapi==1.3.2`

#### 修复

- Webhook：隐私遮罩及其他状态/操作类 msgType（`openCamera`、`closeCamera`、`electricity` 等）视为非告警（仅 `imou_life_event`）；诊断中暴露近期推送 msgType 计数（#66）
- 保留空的 `selected_devices`（不把 `[]` 当未设置）；卸载时关闭 Open API 客户端；删除设备写入 `selected_devices`，避免轮询再次加回
- Webhook 通知/事件优先用 HA 设备注册表名称（`device_name`），而非推送里的 `cname`/`dname`
- Webhook 在解析标识/通知前先 ACK HTTP 200；coordinator 映射无法安全物化白名单时拒绝删除设备

### [1.3.0]

#### 新增

- App Secret 过期时的重新认证流程
- 事件推送 URL 与回调注册失败的修复建议（repair issues）
- 配置条目诊断（密钥已脱敏）
- `integration_type: hub` 与 `quality_scale.yaml`

#### 变更

- Webhook 运行时数据按配置条目隔离；配置条目 v2 迁移补齐缺失的 `webhook_id`
- 配置条目标题显示可读集成名；设备列表为空或不可用时中止
- Switch/select/text 写入触发 coordinator 刷新；switch 类型白名单化
- 所有平台声明 `PARALLEL_UPDATES = 0`

#### 修复

- 卸载不再批量删除设备注册表条目
- 移除不可靠的 camera `is_recording` / `is_streaming` 属性
- `async_get_cached_translations()` 签名兼容（跟进 v1.2.9）

#### 破坏性变更

- 移除自定义实体服务 `imou_life.turn_on`、`turn_off`、`select`、`restart_device`。请改用标准 `switch.turn_on`、`select.select_option`、`button.press`

### [1.2.9]

#### 新增

- 依赖 pyimouapi 1.2.9 做 API 封装（集成代码不再直接写 OpenAPI 路径）
- 完整国际化：Python 中无中文；webhook 消息与配置流程字符串走翻译
- Coordinator `devices_by_key`，设备列表变化时动态热加载
- 事件推送接线改用 `ImouRuntimeData`，不再用 `hass.data`

#### 变更

- 按 Home Assistant 最佳实践重构；对现有用户无破坏性变更
- 更新 README 功能说明（设备选择、事件推送、翻译）

### [1.2.10]

#### 新增

- 安装/选项增加设备选择，可指定纳入哪些乐橙设备
- 增加 webhook 告警推送，支持 Home Assistant 事件与通知
- 增加设备选择与 webhook 边界用例测试

#### 变更

- 将 iotProperty 等非告警推送类型视为通用事件，避免告警通知刷屏

### [1.2.8]

#### 变更

- 升级 pyimouapi 至 1.2.8（经 getIotDeviceDetailInfo 批量属性轮询）

### [1.2.7]

#### 新增

- 贡献治理：PR 模板、CI（lint/test/hassfest/HACS）、CODEOWNERS、CONTRIBUTING
- Issue 自动化：维护者回复后标记 stale 并自动关闭；简化新 Issue 自动回复

#### 变更

- 升级 pyimouapi 至 1.2.7
- 默认设备轮询间隔由 60s 改为 120s
- 更新 GitHub Actions 依赖

### [1.2.0]

#### 新增

- 支持智能插座接入

#### 变更

- 修复若干问题
- 优化 IoT 设备操作逻辑

### [1.1.0]

#### 新增

- 支持乐橙安防设备接入
- 支持多镜头摄像头接入
- 支持集成选项配置
- 支持 binary sensor 实体类型
- 支持实体状态中文翻译

#### 变更

- 修复若干问题
- 优化与平台交互逻辑，离线设备不再请求更新
- 其他代码优化

### [1.0.1]

#### 新增

- 测试用例
- GitHub Action
- Pre-commit hook

#### 变更

- 代码优化

### [1.0.0]

#### 新增

- 首次发布

# REQ-013 / Imou Life 2.0 实现评审与修复设计

**状态**：评审定案  
**日期**：2026-11-16  
**范围**：`Imou-Home-Assistant` 与 `Py-Imou-Open-Api` 的 REQ-013 / 2.0 全量实现  
**修复方式**：分级收敛；先恢复发布基线，再修正确性、完整性和 HA 原生性  

## 1. 评审基线

本评审覆盖：

- V2 `device.capability.list`、property/service/event 读写与实体门控；
- Legacy + V2 消息双轨及 `device.property.report`；
- 云录像、本地录像、HLS、MP4 回退和告警时录像；
- 智见状态、AI 文案、搜索、单日简报、试用弹窗；
- `device_snap_detect` 服务；
- 配置流、翻译、用户文档、测试和打包。

后续明确的产品决定优先于旧规格：

- 简报每次只查询一个自然日，以 `end_date` 选择日期；
- 搜索仍可使用 `days`，默认近 7 天；
- 智见设备选择仅显示已开通智见或智见云存储的摄像头；
- 智见工作台实体和试用按钮挂在账号设备下；
- 试用使用弹窗，前端不可用时必须有可靠后备；
- 试用链接位于“轮询与摄像头”，不新增独立“智见工作台”配置步骤；
- 灯泡产品类型由 `listProductDetail` 的 `deviceType == "Lamp"` 判断；
- 灯泡具体功能再与 `device.capability.list` 的 property 子集相交。

旧规格、计划、README 或 CHANGELOG 与这些决定冲突时，按文档漂移处理，不回退已确认的产品行为。

## 2. 总体结论

REQ-013 的主要结构已存在：

- V2 capability 客户端、target map 与实体读写；
- Media Source、云/本地录像与 HLS；
- 智见状态、AI 文案、搜索、简报和抓图检测；
- Legacy / V2 推送双轨；
- V2 `device.property.report` 属性即时更新。

当前实现仍不能发布，也不能标记为完整：

- 配置流程存在运行时 `NameError`；
- 试用前端模块可阻断无 frontend 环境的集成启动；
- pyimouapi 有未跟踪的运行时依赖文件；
- 两仓全量测试和 Ruff 均未通过；
- V2 过充保护实体、Lamp 判型和部分回退逻辑不完整；
- Webhook 新订阅门控破坏旧配置兼容；
- 本地录像门控、分页和播放恢复不完整；
- 智见 Markdown 错误地放在 HA sensor state；
- 翻译、README、CHANGELOG、guides 与实际 UI 已分叉。

## 3. 已正确实现、应保留的部分

### 3.1 V2 能力与 HA 边界

- 集成通过 pyimouapi 公开方法访问 capability，不操作库私有方法。
- property 使用 V2 批量 get/set；IoT 设备额外传 `productId`，PaaS 设备不传。
- service 能力使用 `device.service.invoke`。
- 宽动态迁移为 select 的方向正确。
- 固定实体特征大多位于 `EntityDescription`。
- Coordinator 与 `async_update_listeners()` 的使用总体符合 HA 模式。

### 3.2 属性推送双轨

- Legacy `iotProperty` 解析 ref 值。
- V2 `device.property.report` 解析顶层完整 propertyId。
- V2 属性报告归入 `capabilityEvent` 订阅，不再误判为点分 V2 告警 eventId。
- `productId` 已进入归一化字段。
- 两条路径最终共用实体属性应用逻辑。
- struct `struct_field` 和常见 `{"enabled": 0|1}` 解包方向正确。

### 3.3 Media 与录像

- 使用 HA 原生 `media_source` 与 Stream。
- 云端使用 bitmap 和片段列表。
- 本地使用 `record.file.bitmap` / `record.file.list`。
- 云、本地播放统一申请 HLS。
- Stream 不可用时存在 MP4 remux 回退。
- 告警时录像调用 HA `camera.record`，不是要求用户配置 YAML 自动化。
- 未接入智见集锦。

### 3.4 智见

- 智见设备选择已过滤普通摄像头。
- 简报已改为单日查询。
- 搜索保留近 N 天。
- AI 文案可进入告警通知。
- 抓图检测只通过库公开接口，图片通知仅接受 HTTPS URL。
- 账号级工作台实体边界合理。

## 4. 第一阶段：恢复可发布基线

### 4.1 配置流程

`config_flow.py` 的登录 schema 使用未导入的 `API_URL_REGIONS`，而文件已导入 `API_URL_LOGIN_REGIONS`。这会使登录、绑定和依赖配置流程的测试直接失败。

修复要求：

- 登录选择器使用当前允许的登录区域集合；
- 补默认区域、国内/海外区域和旧配置值迁移测试。

### 4.2 试用前端不得阻断启动

`insight_frontend.py` 无条件注册静态路径并调用 `frontend.add_extra_js_url()`。frontend 尚未初始化或不存在时会抛异常。文件还直接导入 `homeassistant.components.http`，但 manifest 未声明该依赖。

修复要求：

- 弹窗是可选增强，不是集成启动前提；
- frontend/http 不可用时只记录日志；
- 按钮先创建带固定 notification ID 的持久通知并发送弹窗事件；前端显示弹窗后主动清除该通知，无前端时通知保留；
- 若保留直接组件 import，manifest 依赖必须一致；
- 增加无 frontend 环境加载测试。

### 4.3 打包与代码检查

`pyimouapi/push_dispatch_allowlist.py` 当前未被 Git 跟踪，但 `push.py` 在导入阶段依赖它。

修复要求：

- 文件与调用代码同一提交；
- 添加干净安装/import 打包测试；
- 修复 pyimouapi 当前 Ruff 错误；
- 修复 HA webhook 行宽错误；
- 两仓运行各自完整 pre-commit。

### 4.4 翻译结构

`strings.json` 已将试用链接迁到 `general_settings`，但 en/de/fr/it 仍保留不存在的 `ai_insight` 配置步骤。

修复要求：

- 所有语言与 `strings.json` 具有相同 key 和 placeholder；
- 删除孤立菜单和步骤；
- 用户可见异常、默认提示和抓图检测通知不得硬编码英文。

### 4.5 测试基线

评审时实测：

- pyimouapi：372 通过，1 失败；
- Imou Life：552 通过，31 失败；
- HA 的多数失败由配置流程和 frontend 初始化级联导致，另有独立的翻译、manifest 和 webhook 回归。

第一阶段完成标准是两仓全量测试、Ruff 和 pre-commit 全部通过，不能通过删除有效回归断言来“修绿”。

## 5. 第二阶段：设备分类、V2 属性和推送正确性

### 5.1 Lamp 产品判型

当前代码没有 `listProductDetail`，仅靠 `light.white.*` capability 簇判断灯泡。摄像机可能报告部分照明属性，因此 capability 不能单独证明产品是灯泡。

设计：

1. 设备发现后收集去重的 `productId`。
2. 批量调用 `listProductDetail`，请求 `productIds`。
3. 解析 `data.item[]` 并按 `productId` 缓存。
4. 仅 `deviceType == "Lamp"` 的产品进入灯泡封装。
5. Lamp 类型决定产品身份，capability property 子集决定实际支持的亮度、色温、颜色和场景功能。
6. 不要求 Lamp 同时具备全部彩灯属性。
7. 产品详情查询失败时不根据部分 capability 猜测 Lamp；保留日志并在后续发现周期重试。

### 5.2 IoT / PaaS V2 统一读取

Imou Life 正常路径应为：

- 两类设备都通过 `device.capability.list` 注册；
- property 实体通过 `device.property.get` 批量读取；
- property 写入通过 `device.property.set`；
- IoT 请求携带 `productId`；
- service-backed 状态仍通过 service invoke；
- 在线状态继续使用专用状态接口。

`getIotDeviceDetailInfo` 对 Imou Life 正常路径废弃，但库尚未完全收口：

- `ImouHaDeviceManager.async_get_devices()` 已强制跳过；
- `ImouDeviceManager.async_get_devices()` 默认仍为 `fetch_ability_refs=True`；
- legacy detail/ref 方法仍可被外部调用。

修复要求：

- pyimouapi 2.x 默认 `fetch_ability_refs=False`；
- legacy 参数和方法标记 deprecated；
- Imou Life 不得触达 detail/ref 主路径；
- 为第三方调用者保留显式兼容入口，本次不物理删除；
- 下一个大版本再删除 legacy；
- coordinator 中 `_iot_detail_fetched` 改为表达 V2 状态快照的名称。

### 5.3 过充、电池和计量

- `overcharge_switch` 按当前详设及标准 V2 属性实现为二值 switch；HA `switch.py` 增加实体，旧功率阈值 number 仅保留在明确的 legacy 兼容路径，二者不得共用同一语义键。
- `switch_cnt` 在 `power.outlet.read` 缺字段时，应按规格回退 `control.outlet.switchCount`。
- 明确 `power.battery.info` 与 `power.battery.level` 的合并/优先策略并补测试。

### 5.4 属性消息更新必须保留

继续支持：

- `iotProperty` → legacy ref；
- `device.property.report` → V2 propertyId；
- 两者更新实体后通知 Coordinator listeners。

补充要求：

- 合法属性消息即使 capability 尚未缓存，也应保留通用 `imou_life_event`；
- capability 门控只决定是否写实体，不应过早吞掉整包；
- 未订阅 `capability_event` 时不得应用 V2 属性；
- `msgType` 在归一化入口统一 strip；
- 属性应用仅在真实值被接受/变化时返回 changed；
- 增加 switch、sensor、select、struct 与多通道测试。

### 5.5 推送兼容

- 旧条目缺少 `event_push_types` 时按旧行为迁移；显式空列表才表示用户主动不选。
- lifecycle 不得默认绑定 `device_status`。
- V2 eventId 严格按 capability events 门控。
- Legacy 未知字符串不得因平台新增类型而完全静默丢失；至少保留通用事件和诊断日志。
- 缺失 `msgType` 的行为需要保留既有通用事件兼容，且不得视为告警。

### 5.6 平台决定方式

旧详设写“由 capability dataType 动态决定 HA 平台”，实现则由 target map 的 `ha_platform` 决定。

定案：

- 保留实现方式：map 决定稳定平台；
- capability 元数据只决定实体是否存在、select 选项和数值范围；
- 更新旧规格；
- 避免同一 unique ID 因设备固件返回变化而在 switch/select 间漂移。

## 6. 第三阶段：媒体完整性

### 6.1 能力门控

- 仅在 capability 支持所需 `record.file.*` 服务时显示本地录像目录。
- 云录像目录保持稳定显示；没有录像、无权限或未开通云存时显示空列表并记录可诊断原因，避免云存状态短暂失败造成目录结构抖动。

### 6.2 分页

云录像分页测试目前首批仅一条，却断言发生第二次请求，没有覆盖实现的满页分支。

修复要求：

- 首批构造真实 page size；
- 使用上一批最大 `endTime` 推进；
- 按 `recordId` 去重；
- 游标不前进时记录 warning 并终止；
- 本地录像提供库级聚合分页，避免单日超过 limit 后静默丢片段。

### 6.3 播放恢复

- HLS 公开方法校验同日和合法 `recordType`；
- 播放权限/令牌失败创建 repair，成功后删除；
- HLS session TTL 与签名有效期一致；
- segment 获取失败时最多刷新一次 playlist；
- MP4 remux 遇到过期 m3u8 时重新申请一次播放地址；
- 保持错误有界，禁止无限重试。

### 6.4 目录文档

“告警时录像”是后来增加的第三棵本机媒体树。更新旧详设和 guides，不再只描述云录像/本地录像两棵树。

## 7. 第四阶段：智见与 HA 原生收口

### 7.1 Markdown 状态模型

搜索与简报当前把完整 Markdown 写入 sensor native state。HA state 长度有限，这会导致长结果失败或截断。

修复设计：

- native state 仅保存短状态、结果数或更新时间；
- Markdown 放入 `extra_state_attributes["markdown"]`；
- 搜索属性增加 `result_count`；
- 简报属性增加 `report_date`；
- Dashboard 使用 `state_attr(..., "markdown")`。

### 7.2 单日简报与搜索

- `refresh_ai_insight_briefing` 仅使用 `end_date`，默认当天；
- search 继续使用 `keyword` + `days`；
- 无效日期使用翻译化 `ServiceValidationError`；
- 多账号时必须能解析明确目标 entry；
- 智见设备选择仅显示已开通对应服务的摄像头。

### 7.3 试用弹窗

- 保留账号级试用按钮；
- 弹窗保留为 frontend 增强；
- 按钮创建固定 ID 的持久通知并发送弹窗事件；前端成功显示后清除通知，前端不可用时通知作为后备保留；
- 文本继续按国内/海外区域选择；
- 自定义试用链接位于“轮询与摄像头”；
- 测试无前端、移动端/多浏览器和重复加载场景。

### 7.4 抓图检测与本地化

- 库继续返回结构化检测结果；
- 集成负责将 person/vehicle/pet、未检测到目标和失败信息翻译成 UI 语言；
- 测试默认通知目标、显式通知目标、非 HTTPS 图片和通知正文。

## 8. 文档同步

统一更新：

- `2026-11-10-ha-ai-insight-design.md`；
- `2026-11-11-req-013-v2-detailed-design.md`；
- 2.0 实施计划；
- README；
- CHANGELOG；
- configuration guide；
- Dashboard 示例；
- services.yaml；
- strings 与现有翻译。

删除或改正以下旧说法：

- 简报默认近 7 天；
- 简报使用 `days`；
- 试用仅使用持久通知；
- 存在独立“配置 → 智见工作台”；
- 灯泡仅靠 capability 全集判断；
- 媒体浏览器始终只有两棵树。

## 9. 测试策略

### 9.1 库

- `listProductDetail` 批量、缓存、Lamp/非 Lamp 和失败；
- PaaS / IoT property get 参数差异；
- 默认不调用 `getIotDeviceDetailInfo`；
- Lamp 部分 capability；
- 过充、电池、switch count 回退；
- V2 property report 的标量、struct 与类型；
- 云/本地满页分页；
- HLS 参数和过期刷新；
- 干净打包/import。

### 9.2 集成

- 无 frontend 加载；
- 登录区域 schema；
- 旧配置迁移；
- capability-only 属性推送；
- lifecycle/未知消息/缺失 msgType；
- 长 Markdown 属性；
- 单日简报；
- 智见设备过滤；
- 翻译结构；
- 本地录像能力门控；
- playback repair；
- Lamp、IoT 插座、PaaS IPC 实体快照。

### 9.3 真机

至少覆盖：

- PaaS IPC；
- IoT 插座；
- `deviceType=Lamp` 灯泡；
- capability list 缺失/失败；
- 云录像；
- SD 卡本地录像；
- 智见状态、搜索和单日简报；
- Legacy 告警、V2 eventId、`iotProperty` 和 `device.property.report`。

## 10. 完成定义

满足以下全部条件才能标记 REQ-013 完成：

- 两仓没有遗漏的未跟踪源码；
- 两仓全量 pytest、Ruff、pre-commit 全绿；
- pyimouapi 干净安装后可 import；
- 无 frontend 的 HA 可以加载集成；
- Imou Life 正常路径没有 `getIotDeviceDetailInfo` 请求；
- PaaS IPC、IoT 插座和 Lamp 的实体、读取和控制正确；
- Legacy / V2 属性消息都能即时更新实体；
- 旧配置升级后不静默丢告警；
- 云/本地录像可完整分页和播放；
- 长搜索/简报不写入 HA state；
- 单日简报日期准确；
- 翻译和用户文档与实际 UI 一致；
- 真机验收清单有结果记录。

## 11. 实施顺序

1. 发布基线与 CI。
2. Lamp / V2 属性 / 推送正确性。
3. Media 完整性。
4. 智见与 HA 原生收口。
5. 文档、全量自动测试和真机验收。

每阶段独立验证后再进入下一阶段；不把修复扩展到与 REQ-013 无关的重构。

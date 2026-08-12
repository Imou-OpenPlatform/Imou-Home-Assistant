# CHANGELOG
## [1.3.4]
### Breaking
- Home Assistant 2025.4 or newer is required. Cores below that no longer see this integration in HACS
- Select option states use friendly keys (`home`/`away`/`disarm`, `mute`/`low`/`medium`/`high`, night-vision string keys) matching pyimouapi 1.3.4. Automations calling `select.select_option` with the old numeric values (`"0"`, `"1"`, …) must be updated
- The motion detection switch is removed for product_id `FKX9UYL4`. That model advertises the capability but cannot serve it, so the entity never worked; it will show as unavailable and can be deleted. `camera.motion_detection_enabled` for those devices now reflects only human detection

### Added
- Bind devices to the open-platform account from Configure → Manage devices (serial + verification code); setup no longer aborts when the account has zero devices (bind now or finish with an empty selection)
- **Enable status polling** option in Configure → General; disable to stop background status refreshes and save Open API quota (#15)

### Changed
- Config / options flows surface Imou Open API `code`/`msg` in the UI (e.g. `OP1013` quota exceeded) instead of a generic “request failed” (#67)
- All entity writes (switch, select, button, text) use optimistic local updates and no longer trigger an immediate full cloud poll
- Errors raised while operating a device are translated, so the UI shows them in your language instead of the raw English message from the API
- Listing the account now runs on its own ten minute clock rather than on every status poll. Status still refreshes at the interval you configured; only the check for devices added to or removed from the account slowed down, which is where most of the Open API quota was going. A device added in the Imou app appears within ten minutes
- Settings that configure a device (detection switches, volume, night vision, thresholds, timers, restart) are filed under the device's configuration section instead of sitting among its primary controls. Entities in that section are hidden from auto-generated dashboards and are not exposed to Assist by default; the entities themselves are unchanged, so anything referring to them by entity id keeps working
- Invalid credentials now end the polling and ask you to sign in again, rather than retrying a secret the integration already knows is refused
- Setting up an account that holds no devices records no device filter at all, instead of an empty one. A device bound later from the Imou app is picked up automatically; previously it was filtered out for good with nothing to indicate why
- Depend on `pyimouapi==1.3.5`, which brings concurrent status reads, a per-host connection cap so snapshot downloads cannot stall status polling, credentials kept out of debug logs, and several connection-leak and paging fixes
- A camera that cannot produce a snapshot reports why, in your language, instead of showing a blank tile
- One unreadable accessory no longer leaves the whole account showing as unavailable
- Less log noise: the device filter, and devices that are asleep, are logged at debug rather than info
- Issue templates: expand Feature request and Question forms; use `feature` label aligned with `[Feature]` titles

### Fixed
- The `webhook` component is declared in the manifest. Event push registers a webhook and the config flow generates its URL, so on an installation that did not already load `webhook` for another reason this could fail
- The API session and the webhook registration are released when setup fails or is retried, instead of leaking across the retry
- `control_move_ptz` accepts a target entity and its `duration` limit matches what the service actually allows
- A rotated or revoked App Secret is noticed on the next status poll and opens the re-authentication prompt. Because listing the account moved to a ten minute clock, and because the library logged credential errors rather than reporting them, this could otherwise go unnoticed for ten minutes — or indefinitely with status polling turned off — while every entity kept showing its last known value as current
- Turning event push off in the options now tells the Imou cloud to stop pushing. The new setting was already saved by the time the integration reloaded, so it concluded push had never been on and left the cloud callback registered, spending Open API quota on messages that were then discarded
- The options can be saved when the device list cannot be fetched. Listing the account was the last step and had no way past it, so a quota-exceeded or unreachable account discarded every change — including the polling interval and event push settings you would want to change in exactly that situation
- A device removed from the account no longer breaks the update for the remaining entities. Its select and text entities raised while Home Assistant collected their attributes, which happens before availability is checked
- Re-authenticating successfully shows a confirmation instead of a blank message
- Deleting one camera of an NVR, or one lens of a multi-lens camera, no longer removes the others. Those arrive from the account as one device carrying several channels, and each channel becomes its own device here; the exclusion recorded on deletion is per account device, so it took the siblings with it along with any names, areas, and automations attached to them. Deleting a single channel is now refused with an explanation in the log — deselect the device in the options to stop polling it, or disable the channel's entities to hide it

## [1.3.3]
### Added
- `select.collection_point` for PTZ preset query and goto (#53, #71) — lists presets from the cloud/device; placeholder **Select a preset…** / **选择收藏点…** when current position is unknown; use standard `select.select_option` in automations
- `button.siren_start` and `button.siren_stop` for devices with Siren capability or IoT refs `25500`/`22200`

### Changed
- Depend on `pyimouapi==1.3.3`
- Select and switch commands no longer trigger an immediate full cloud poll; UI updates from optimistic local state (saves API quota)

## [1.3.2]
### Changed
- Options flow: split Configure into General, Event push, and Devices steps; group event push settings (callback URL, message types, notifications)
- Config flow login step aligned with Core imou (region dropdown, shared error keys)
- Sensor, select, and switch platforms use EntityDescription whitelists aligned with Core

## [1.3.1]
### Changed
- Event push always syncs to the Imou app (`basePush=1`); removed the Base push option from Configure
- Webhook `msg_type` uses top-level `msgType` only; still expose `product_id` (`pid`) and `outputData`; treat `iotEvent` / `sirenOn` / `sirenOff` as alarms
- Webhook: resolve numeric / `iotEvent` push types to product-model event identifiers via pyimouapi 1.3.2 (alarm classification still uses top-level `msgType`)
- Depend on `pyimouapi==1.3.2`

### Fixed
- Webhook: treat privacy-mask and other status/ops msgTypes (`openCamera`, `closeCamera`, `electricity`, …) as non-alarm (`imou_life_event` only); expose recent push msgType counts in diagnostics (#66)
- Preserve empty `selected_devices` (do not treat `[]` as unset); close Open API client on unload; persist device removal into `selected_devices` so poll does not re-add it
- Webhook notify/events prefer HA device registry name (`device_name`) over push `cname`/`dname`
- Webhook ACKs HTTP 200 before identifier resolve/notify; refuse device removal when the coordinator map cannot safely materialize an allow-list

## [1.3.0]
### Added
- Reauth flow when App Secret expires
- Repair issues for event push URL and callback registration failures
- Config entry diagnostics (redacted secrets)
- `integration_type: hub` and `quality_scale.yaml`

### Changed
- Webhook runtime data isolated per config entry; config entry v2 migration adds missing `webhook_id`
- Config entry titles show readable integration name; abort when device list is empty or unavailable
- Switch/select/text writes trigger coordinator refresh; switch types whitelisted
- All platforms declare `PARALLEL_UPDATES = 0`

### Fixed
- Unload no longer bulk-removes device registry entries
- Removed unreliable camera `is_recording` / `is_streaming` properties
- `async_get_cached_translations()` signature compatibility (from v1.2.9 follow-up)

### Breaking
- Removed custom entity services `imou_life.turn_on`, `turn_off`, `select`, and `restart_device`. Use standard `switch.turn_on`, `select.select_option`, and `button.press` instead.

## [1.2.9]
### Added
- pyimouapi 1.2.9 dependency for API encapsulation (no direct OpenAPI paths in integration code)
- Full i18n: no Chinese in Python; translations for webhook messages and config flow strings
- Coordinator `devices_by_key` and dynamic device hot-load when device lists change
- `ImouRuntimeData` replaces `hass.data` for event push wiring

### Changed
- Refactor aligned with Home Assistant best practices; no breaking changes for existing users
- README features section updated (device selection, event push, translations)

## [1.2.10]
### Added
- Add setup/options device selection so users can choose which Imou devices to include
- Add webhook alarm push support with Home Assistant events and notifications
- Add tests for device selection and webhook edge cases

### Changed
- Treat non-alarm push types such as iotProperty as generic events to avoid alarm notification spam

## [1.2.8]
### Changed
- Bump pyimouapi to 1.2.8 (batch property polling via getIotDeviceDetailInfo)

## [1.2.7]
### Added
- Contributor governance: PR template, CI (lint/test/hassfest/HACS), CODEOWNERS, and CONTRIBUTING guide
- Issue automation: stale label and auto-close after maintainer reply; simplified new-issue auto-reply

### Changed
- Bump pyimouapi to 1.2.7
- Default device polling interval changed from 60s to 120s
- GitHub Actions dependencies updated

## [1.2.0]
### Added
- Support the access of smart sockets
### Changed
- Fixed some bugs
- Optimize the operation logic of IoT devices

## [1.1.0]
### Added
- Support for Imou home security device integration
- Support for multiple lens camera integration
- Support for integrated option configuration
- Support for binary sensor entity type
- Support for Chinese translation of entity status

### Changed
- Fixed some bugs
- Optimized interaction logic with the platform, offline devices will no longer request updates
- Other code optimizations

## [1.0.1]
### Added
- Test case
- Github action
- Pre-commit hook

### Changed
- Code optimization

## [1.0.0]

### Added

- First release

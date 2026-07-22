# CHANGELOG
## [1.3.1]
### Changed
- Event push always syncs to the Imou app (`basePush=1`); removed the Base push option from Configure
- Webhook `msg_type` uses top-level `msgType` only; still expose `product_id` (`pid`) and `outputData`; treat `iotEvent` / `sirenOn` / `sirenOff` as alarms
- Webhook: resolve numeric / `iotEvent` push types to product-model event identifiers via pyimouapi 1.3.2 (alarm classification still uses top-level `msgType`)
- Depend on `pyimouapi==1.3.2`

### Fixed
- Webhook: treat privacy-mask and other status/ops msgTypes (`openCamera`, `closeCamera`, `electricity`, …) as non-alarm (`imou_life_event` only); expose recent push msgType counts in diagnostics (#66)
- Preserve empty `selected_devices` (do not treat `[]` as unset); close Open API client on unload; persist device removal into `selected_devices` so poll does not re-add it

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

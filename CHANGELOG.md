# CHANGELOG
## [1.3.0]
### Added
- pyimouapi 1.2.9 dependency for API encapsulation (no direct OpenAPI paths in integration code)
- Full i18n: no Chinese in Python; translations for webhook messages and config flow strings
- Coordinator `devices_by_key` and dynamic device hot-load when device lists change
- `ImouRuntimeData` replaces `hass.data` for event push wiring

### Changed
- Refactor aligned with Home Assistant best practices; no breaking changes for existing users

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

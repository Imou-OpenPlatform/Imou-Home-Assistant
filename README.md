# Imou Home Assistant Component Integration

[![HACS Default][hacs-badge]][hacs-url]
[![GitHub Release][release-badge]][release-url]
[![HACS Downloads][downloads-badge]][release-url]
[![GitHub Stars][stars-badge]][repo-url]
[![Active Installs][installs-badge]][analytics-url]

## Introduction

Imou Open Platform offers an open-source Imou python component. By integrating this component into the Home Assistant service, developers can access live preview, control devices, and view device statuses of Imou devices. Additionally, developers have the ability to extend the functionality of the Imou component by creating additional features.

This integration enables bidirectional communication between Home Assistant and Imou ecosystem devices via the Imou Open Platform API.

## Installation
1: <b>Register an Imou Account.</b> Visit the [Imou Open Platform official website](https://open.Imoulife.Com) to register or log in to your Imou account;

2: <b>Generate AppId and AppSecret.</b> After registration, proceed to the [Official Console](https://open.Imoulife.Com/consoleNew/myApp/appInfo) to complete your application details and generate an AppId and AppSecret;

<img src="https://raw.githubusercontent.com/Imou-OpenPlatform/Imou-Home-Assistant/refs/heads/main/assets/images/appMsg.png" width="70%">

3: <b>Navigate to HACS, search for `Imou Life`, and install the Imou component.</b> On the component login page, enter the obtained AppId and AppSecret. The URL address can be referenced from the [Interface Domain Name Description](https://open.Imoulife.Com/book/http/develop.Html) to obtain the recommended optimal domain URL;

<img src="https://raw.githubusercontent.com/Imou-OpenPlatform/Imou-Home-Assistant/refs/heads/main/assets/images/login.png" width="70%">

4: <b>Integration completed.</b> At this point, you can view the devices under the Imou account.
<img src="https://raw.githubusercontent.com/Imou-OpenPlatform/Imou-Home-Assistant/refs/heads/main/assets/images/list.png" width="70%">

>Note: <br>
>The components is integrated with the Imou Open Platform for cloud-based remote device viewing. <br>
>The cloud API requests and video playback within the components will consume the resource quota under the AppId account. <br>
>You may check the account resource status in the [My Resources of the Open Platform](https://open.imoulife.com/consoleNew/resourceManage/myResource).

## Features
* **Camera Function Management**
  - Information and status display (device name, online status, storage status, battery level, etc.)
  - Live video preview
  - PTZ control
  - Motion detection configuration
  - Human detection configuration
  - Privacy mode configuration
  - Night vision mode configuration
  - White light alarm configuration
  - Audio capture configuration
  - Abnormal sound alarm configuration
  - Device reboot
* **Alarm Sensor Smart Device Management**
  - Information and status display (device name, online status, arming mode, battery level, etc.)
  - Alarm volume configuration
  - One-click alarm mute
  - Indicator light switch configuration
  - Temperature & humidity monitoring
  - Device reboot
* **Energy Smart Device Management**
  - Information and status display (device name, power consumption, online status)
  - Socket switch and countdown settings
  - Socket indicator configuration
  - Socket power configuration

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
| **GitHub stars** | GitHub | Repository stargazers |

### Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=Imou-OpenPlatform/Imou-Home-Assistant&type=Date&theme=dark" />
  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=Imou-OpenPlatform/Imou-Home-Assistant&type=Date" />
  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=Imou-OpenPlatform/Imou-Home-Assistant&type=Date" />
</picture>

<!-- Badge references -->
[hacs-badge]: https://img.shields.io/badge/HACS-Default-orange.svg?logo=HomeAssistantCommunityStore&logoColor=white&style=flat-square
[release-badge]: https://img.shields.io/github/v/release/Imou-OpenPlatform/Imou-Home-Assistant?style=flat-square&label=Release
[downloads-badge]: https://img.shields.io/github/downloads/Imou-OpenPlatform/Imou-Home-Assistant/total.svg?style=flat-square&label=HACS%20downloads
[stars-badge]: https://img.shields.io/github/stars/Imou-OpenPlatform/Imou-Home-Assistant?style=flat-square
[installs-badge]: https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=Active%20installs&suffix=%20installs&cacheSeconds=21600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.imou_life.total
[hacs-url]: https://github.com/hacs/integration
[release-url]: https://github.com/Imou-OpenPlatform/Imou-Home-Assistant/releases
[repo-url]: https://github.com/Imou-OpenPlatform/Imou-Home-Assistant
[analytics-url]: https://analytics.home-assistant.io/custom_integrations.json

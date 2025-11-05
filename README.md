# Imou Home Assistant Component Integration  

[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg?logo=HomeAssistantCommunityStore&logoColor=white)](https://github.com/hacs/integration)

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


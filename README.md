<div align="center">

<img src="icon.png" alt="Kernel" width="96" height="96">

# Kernel

**A Windows 11 system utility for live monitoring, performance tweaks, debloating, storage management and one-click app installs.**

<br>

[![Support on Ko-fi](https://img.shields.io/badge/Support%20on-Ko--fi-7c3aed?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/mindzy)
&nbsp;
[![Report a bug](https://img.shields.io/badge/Report-a%20bug-2b2b2b?style=for-the-badge&logo=github&logoColor=white)](https://github.com/mindzy007/kernel/issues/new)
&nbsp;
[![Stars](https://img.shields.io/github/stars/mindzy007/kernel?style=for-the-badge&logo=github&logoColor=white&color=2b2b2b)](https://github.com/mindzy007/kernel/stargazers)

</div>

<br>

## Overview

Kernel puts the tools you would normally hunt for across Settings, the registry, scripts and a dozen download pages into a single, fast app. Every action is one click, every change is logged, and anything that can be undone has a **Revert** button next to it.

Kernel is free, with no ads and no tracking. If it saves you time, an optional donation on [Ko-fi](https://ko-fi.com/mindzy) helps fund fixes and new features.

<br>

## Features

### Tweaks
Privacy, appearance and input changes, each applied (and reverted) with a single click.

- Minimize telemetry, disable ads and suggestions, remove Bing from search, disable Nearby sharing
- Hide widgets from the taskbar, disable lock screen widgets, enable *End Task* in the taskbar, taskbar date format
- Force dark mode, disable Windows sounds, Big Sur cursor, MacType text rendering, Notepad settings
- Disable enhanced mouse precision and Sticky Keys
- Disable Smart App Control, enable Group Policy Editor
- Remove OneDrive, clear Start Menu pins, system cleanup

### Performance
- Ultimate Performance power plan and Game Mode
- Hardware-accelerated GPU scheduling
- Disable Core isolation, Windows Recall and the Xbox Game Bar
- System file repair and maintenance
- NVIDIA profile and shader cache clearing
- Display controls: gamma, contrast, vibrance and refresh rate

### Applications
- 60+ apps across browsers, communication, gaming, media, developer tools, utilities and hardware/firmware software
- Downloads come straight from the publisher, checked against their hash where one is published
- Latest version and file size shown before you download
- Install silently or just download the installer
- Built-in download manager to track, cancel and open downloads
- One-click install or repair of the Microsoft Visual C++ redistributables (2005-2022, x86 and x64)

### Storage
- Interactive usage chart for each drive with a breakdown by apps, games, documents, system and free space
- Biggest folders per drive
- Filter apps and games by drive and sort by biggest or smallest
- Uninstall large apps and games directly
- Clean up temporary files, prefetch data and the Recycle Bin

### Specifications
- Live CPU, GPU, memory and system metrics: clock speeds, temperatures, power draw, fan speeds and load
- Per-core CPU temperatures
- Full hardware summary you can copy in one click

### Activity and Settings
- Activity log of every action taken during the session
- Smooth scrolling, CPU temperature display mode and title bar button style
- Built-in update checker and bug reporting

<br>

## Requirements

| Requirement | Details |
| --- | --- |
| **OS** | Windows 11 (64-bit). Some tweaks need a recent build, such as 22H2 or newer |
| **Permissions** | Administrator rights (Kernel asks on launch) |
| **Sensors** | The optional [PawnIO](https://pawnio.eu) driver is needed for CPU temperature, power and clock readings. The installer can set it up for you |

<br>

## Installation

### Installer (recommended)
1. Download `Kernel-Setup-<version>.exe` from the [latest release](https://github.com/mindzy007/kernel/releases/latest).
2. Run it and follow the prompts.

### Run from source
```bat
git clone https://github.com/mindzy007/kernel.git
cd kernel
run.bat
```
`run.bat` installs the required Python package (PySide6) and starts the app.

### Build it yourself
Requires Python 3, the .NET 8 SDK and [Inno Setup](https://jrsoftware.org/isinfo.php).

```bat
build-installer.bat   :: builds the installer into the installer folder
build-portable.bat    :: builds a portable copy for quick testing
```

<br>

## Safety

Kernel changes system settings, so please read this before you start.

- Create a **restore point** before applying tweaks.
- Tweaks marked **Cannot be reverted** (for example Remove OneDrive and System Cleanup) cannot be undone from the app.
- Everything you apply is recorded in the Activity log.
- Kernel is provided as is, without warranty. You use it at your own risk.

<br>

## Reporting bugs

Open an [issue](https://github.com/mindzy007/kernel/issues/new), or use **Settings, Report a bug** inside the app. It opens a pre-filled issue in your browser with your app and Windows version already included. A free GitHub account is needed to submit.

<br>

## Support the project

Kernel is free. If you would like to support it, you can do so on Ko-fi. It is entirely optional and always appreciated.

[![Support on Ko-fi](https://img.shields.io/badge/Support%20on-Ko--fi-7c3aed?style=for-the-badge&logo=kofi&logoColor=white)](https://ko-fi.com/mindzy)

<br>

## Credits

- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (MPL-2.0) powers the hardware readings
- [PawnIO](https://pawnio.eu) provides the low-level driver for sensor access
- [Allo](https://github.com/joshpuckett/allo) by Josh Puckett is the app font (SIL Open Font License, the full text is in [`fonts/OFL.txt`](fonts/OFL.txt))
- Built with Python and [Qt for Python (PySide6)](https://doc.qt.io/qtforpython-6/)

Third-party license notices are included in the `monitor-helper` folder.

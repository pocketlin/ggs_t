# ADB 环境搭建与 Android 设备连接 Q&A 记录

> 日期: 2026-07-04 | 环境: Windows, Git Bash, Python venv

---

## 1. Git 推送 platform-tools 和 links.txt

**操作:** 将 `links.txt` 和 `platform-tools/` 目录提交并推送到远程仓库。

```powershell
git add platform-tools/ links.txt
git commit -m "Add platform-tools and links.txt"
git push
```

**结果:** 推送成功，15 个文件已上传至 `origin/main`。

---

## 2. 使用 ADB 连接 Android 模拟器并获取网络信息

**操作:** 通过 `platform-tools\adb.exe` 连接模拟器。

```powershell
.\platform-tools\adb.exe devices
.\platform-tools\adb.exe -s emulator-5554 shell ip addr show
```

**模拟器网络信息:**

| 接口    | IP 地址         | 说明       |
|---------|-----------------|------------|
| lo      | 127.0.0.1/8     | 回环       |
| eth0    | 10.0.2.15/24    | 蜂窝网络   |
| wlan0   | 10.0.2.16/24    | WiFi       |

| 项目         | 值                         |
|-------------|----------------------------|
| 设备型号     | sdk_gphone64_x86_64 (Google) |
| SDK 版本     | 35                         |
| 网关         | 10.0.2.2                   |
| DNS          | 10.0.2.3, fec0::3          |

---

## 3. 宿主机是否能直连 Android（网络包直达）

**结论: 不能（针对模拟器）。**

- 模拟器使用 QEMU **SLiRP 用户态 NAT**，`10.0.2.0/24` 仅在模拟器内部可见。
- 模拟器 → 宿主机: 通过 `10.0.2.2` 可达（映射到宿主机回环）。
- 宿主机 → 模拟器: **无路由，100% 丢包**。

| 宿主机 IP 段          | 接口           |
|-----------------------|----------------|
| 192.168.31.94/24      | WLAN           |
| 192.168.225.1         | VMware VMnet8  |
| 192.168.119.1         | VMware VMnet1  |

> 物理机在同子网时（如 `192.168.31.x/24`），网络包可直传。

---

## 4. ADB WiFi 远程连接物理 Android 设备

**目标:** realme RMX3820, Android 16 (SDK 36)

### 4.1 配对

Android 11+ 无线调试需要先配对（配对端口 ≠ 连接端口）。

```powershell
# 使用配对码 + 配对端口
.\platform-tools\adb.exe pair 192.168.31.112:38593 606410
# 输出: Successfully paired to 192.168.31.112:38593
```

### 4.2 连接

```powershell
# 使用连接端口
.\platform-tools\adb.exe connect 192.168.31.112:39535
# 输出: connected to 192.168.31.112:39535
```

### 4.3 设备信息

| 项目       | 值                     |
|-----------|------------------------|
| 设备       | realme RMX3820         |
| Android    | 16 (SDK 36)            |
| WiFi IP    | 192.168.31.112/24      |
| 宿主机 IP  | 192.168.31.94/24       |

---

## 5. SSH 连接可行性

**结论: 不能。** 设备未运行 sshd/dropbear，22 端口未开放。

替代方案:
- 使用 `adb shell` 获得等效 shell 体验
- 或安装 Termux / SSH Server 应用

---

## 6. Python ADB 封装库

当前 venv 中**无**已安装的 ADB 库。主流可选库:

| 库名              | 安装命令                      | 特点                       |
|-------------------|-------------------------------|----------------------------|
| `pure-python-adb` | `pip install pure-python-adb` | 纯 Python，无需系统 ADB      |
| `adb-shell`       | `pip install adb-shell`       | 轻量，专注 shell 交互        |
| `ppadb`           | `pip install ppadb`           | 简单封装，Client/Device API |

**pure-python-adb 示例:**

```python
from adb.client import Client as AdbClient

client = AdbClient(host="127.0.0.1", port=5037)
device = client.device("192.168.31.112:39535")
print(device.shell("getprop ro.product.model"))
```

---

## 关键命令速查

```powershell
# ADB 路径
D:\projects\pycharmpro\ggs_t\platform-tools\adb.exe

# Python 路径
D:\projects\pycharmpro\ggs_t\.venv\Scripts\python.exe

# 查看设备
adb devices

# WiFi 配对 (Android 11+)
adb pair <ip>:<配对端口> <配对码>

# WiFi 连接
adb connect <ip>:<连接端口>

# 执行 shell
adb -s <device> shell <command>

# 断开
adb disconnect <ip>:<port>
```

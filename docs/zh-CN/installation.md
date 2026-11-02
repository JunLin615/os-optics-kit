# 安装

[用户指南](README.md) · [English](../en/installation.md) · 简体中文

OS Optics 支持 Windows、macOS 和 Linux，要求 **Python 3.11 及以上版本**。编写 Python 脚本或使用 Notebook 时选择 pip；只需要独立命令行工具时可选择 uv。

## 让示例与安装版本一致

本仓库的示例和指南使用 os-optics **0.10.0**，该版本及其 wheel、源码包哈希和测试环境记录在
[engine-baseline.json](../../engine-baseline.json)。请在全新环境中安装该精确版本：

```bash
python -m pip install "os-optics[protected]==0.10.0"
```

[对齐检查](../engine-alignment.md)会在该安装上运行示例；运行示例前先用
`os-optics doctor` 查看已安装版本。

## Python 环境

创建虚拟环境：

```bash
python -m venv .venv
```

macOS 或 Linux 激活方式：

```bash
source .venv/bin/activate
```

Windows PowerShell 激活方式：

```powershell
.venv\Scripts\Activate.ps1
```

安装并检查引擎：

```bash
python -m pip install "os-optics[protected]"
os-optics doctor
```

命令行也可以通过 `python -m os_optics` 调用。如果 PowerShell 无法激活环境，可直接运行 `.venv\Scripts\python.exe -m pip install "os-optics[protected]"` 和 `.venv\Scripts\python.exe -m os_optics doctor`。

`protected` 扩展支持在硬件流程中打开装配预览和装配详情。只进行光学设计与仿真时，安装 `python -m pip install os-optics` 即可。

## 使用 uv 安装独立命令行工具

安装 [uv](https://docs.astral.sh/uv/) 后运行：

```bash
uv tool install "os-optics[protected]"
os-optics doctor
```

如果终端找不到命令，运行 `uv tool update-shell`，然后重新打开终端。也可以临时运行一条命令：

```bash
uvx --from "os-optics[protected]" os-optics doctor
```

## 准备光学目录

```bash
os-optics catalog sync
os-optics catalog status
```

目录会下载到本地存储中，后续仿真可以使用已保存的版本，也支持离线运行。`doctor` 检查本地安装是否就绪；`catalog sync` 从 OpenSci 获取当前目录。

## 升级

使用 pip 安装时：

```bash
python -m pip install --upgrade "os-optics[protected]"
```

使用 uv 工具安装时：

```bash
uv tool upgrade os-optics
```

0.9.0 及更早版本以 `opensci-engine` 名称发布（命令 `opensci-engine`，`import opensci_engine`）。
以该名称安装的环境同样运行 `python -m pip install --upgrade "os-optics[protected]"`，两个软件包没有共用文件。
之后使用 `os-optics` 命令和 `import os_optics`。以该名称通过 `uv tool` 安装的环境运行
`uv tool install "os-optics[protected]"`，之后使用 `os-optics`。

运行 `os-optics doctor` 查看已安装版本。需要固定这些示例对应的版本时，安装 `"os-optics[protected]==0.10.0"`。

[engine-baseline.json](../../engine-baseline.json)记录已验证的版本和测试环境。维护者可以通过[版本对齐流程](../engine-alignment.md)检查其他已发布版本；候选版本检查通过后也不会自动修改基线。

## 常见安装问题

| 现象 | 处理方式 |
| --- | --- |
| 安装提示 Python 版本不符 | 检查 `python --version`，使用 Python 3.11+ 或 uv 安装方式 |
| 找不到 `os-optics` 命令 | 激活虚拟环境，或使用 `python -m os_optics` |
| doctor 显示 `DOWNLOAD_ON_FIRST_USE` | 运行 `os-optics catalog sync`，或开始使用目录创建设计 |
| 目录下载失败 | 用 `os-optics catalog check` 检查连接；离线导入方式见[目录指南](catalog.md) |
| 服务要求更新引擎 | 升级软件包、更新项目目录版本，重新运行设计后再提交 |

下一步：[运行第一个光学设计](getting-started.md)。

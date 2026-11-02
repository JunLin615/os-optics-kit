# 从设计到硬件

[用户指南](README.md) · [English](../en/hardware.md) · 简体中文

[OpenSci](https://opensci-optics.com/) 将光学设计与元件采购、装配连接起来。先使用引擎在本地完成光学系统设计，再通过 OpenSci Account 获取元件清单、价格与硬件配置。

## 工作流程

**设计 → 仿真 → 本地验证 → 元件与装配配置 → 订单 → 硬件交付**

根据实验需要选择购买方式：

| 方式 | 购买内容 |
| --- | --- |
| 光学元件 | 设计选定的光学元件，并提供光学布局图 |
| 实验装配 | 光学元件及其机械装配配置 |
| 使用已有光学平台 | 采用自己的光学平台，不购买新的面包板 |
| 装配服务 | 硬件及可选的 OpenSci 装配服务 |

## 连接 Account

从 [OpenSci 官网](https://opensci-optics.com/) 进入。Account 提供服务地址与 Agent Token 配置方式。将 `OPENSCI_API_URL` 设置为 Account 显示的地址，按页面完成 Token 配置，然后检查连接：

```bash
os-optics account status
```

[安装指南](installation.md)中的 `protected` 扩展支持打开装配预览和装配详情。

## 获取配置方案

使用准备采购的设计对应的已保存运行结果。服务根据提交的设计及商业要求判断是否可采购。
科学目标判定单独保留：目标满足并不代表可下单，可选目标未满足也不应被工具另行设为采购门槛。

以下命令请求配置，不创建或支付订单：

```bash
os-optics resolve run
```

返回结果包含 `resolution_id`、客户购买 BOM、当前价格和装配配置。也可以选择：

```bash
os-optics resolve run --optics-only
os-optics resolve run --no-breadboard
os-optics resolve run --assembly-service
```

将 `RESOLUTION_ID` 替换为返回的标识，查看选定的方案及预览：

```bash
os-optics resolution show RESOLUTION_ID --locale zh-CN
os-optics assembly preview RESOLUTION_ID --view
```

方案会说明元件定位和保留自由度的实现方式。使用 `--locale en` 可以查看英文内容。装配预览适用于包含装配的配置。

## 创建订单

在 OpenSci Account 中选择收货地址，核对元件清单、总价和协议。以下命令会为选定方案创建订单：

```bash
os-optics order create RESOLUTION_ID
```

按订单说明完成付款后，使用 `os-optics order report-transfer ORDER_ID` 报告已转账。OpenSci 确认到账后，提供相应图纸交付物。

装配订单生成详细装配图：

```bash
os-optics assembly drawing RESOLUTION_ID --out drawing --locale zh-CN
```

仅购买光学元件的订单生成光学布局图：

```bash
os-optics order layout ORDER_ID --out layout --locale zh-CN
```

使用 `--locale en` 生成英文图纸。图纸提供 SVG 和 PDF 格式。

完整产品与服务请访问 **[opensci-optics.com](https://opensci-optics.com/)**。

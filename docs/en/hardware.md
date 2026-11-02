# From design to hardware

[User guide](README.md) · English · [简体中文](../zh-CN/hardware.md)

[OpenSci](https://opensci-optics.com/) connects optical design to component purchasing and assembly. Use the engine to develop the optical system locally, then continue with your OpenSci Account to obtain a component list, pricing, and a hardware configuration.

## The workflow

**Design → simulate → Local Validation → component and assembly configuration → order → hardware delivery**

Choose the route that fits your experiment:

| Route | What you purchase |
| --- | --- |
| Optical components | The optics selected by the design, with an Optical Layout Drawing |
| Experimental assembly | Optics and their mechanical assembly configuration |
| Existing optical table | A configuration that uses your own table, without a new breadboard |
| Assembly service | Hardware with the available OpenSci assembly service |

## Connect your Account

Start at the [OpenSci website](https://opensci-optics.com/). Your Account provides the service URL and Agent-token setup. Configure `OPENSCI_API_URL` with that URL and complete the token setup shown in your Account. Then inspect the connection:

```bash
os-optics account status
```

The `protected` extra from the [installation guide](installation.md) supports opening assembly previews and details.

## Get a configuration

Use the saved run for the design you want to purchase. The service evaluates purchase
eligibility from the submitted design and its commercial requirements. Scientific
target assessments remain separate: a met target does not establish orderability,
and an unmet optional target is not a helper-side purchase veto.

The command requests a configuration; it does not create or pay for an order:

```bash
os-optics resolve run
```

The response contains a `resolution_id`, the Customer Purchase BOM, current prices, and the assembly configuration. Variants include:

```bash
os-optics resolve run --optics-only
os-optics resolve run --no-breadboard
os-optics resolve run --assembly-service
```

Inspect the chosen resolution and its preview, replacing `RESOLUTION_ID` with the returned identifier:

```bash
os-optics resolution show RESOLUTION_ID --locale en
os-optics assembly preview RESOLUTION_ID --view
```

The resolution describes how component positioning and retained degrees of freedom are implemented. Use `--locale zh-CN` for Chinese text. The assembly preview applies to configurations that include an assembly.

## Place the order

Choose the shipping address in your OpenSci Account and review the component list, total, and agreement. The following command creates the order for the selected resolution:

```bash
os-optics order create RESOLUTION_ID
```

Follow the payment instructions in the order. After transferring payment, report the transfer with `os-optics order report-transfer ORDER_ID`. OpenSci confirms receipt and makes the corresponding drawing deliverables available.

For an assembly order:

```bash
os-optics assembly drawing RESOLUTION_ID --out drawing --locale en
```

For an optics-only order:

```bash
os-optics order layout ORDER_ID --out layout --locale en
```

Use `--locale zh-CN` to produce Chinese drawings. Drawings are available as SVG and PDF.

Explore the platform and available products at **[opensci-optics.com](https://opensci-optics.com/)**.

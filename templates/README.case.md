# {{title}}

{{summary}}

![{{image_alt}}](images/{{image}})

## Run it

```bash
os-optics-kit cases copy {{name}} --dest .
cd {{name}}
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics view run
```

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
{{results_table}}

Verified with os-optics {{engine}} (catalog {{catalog}}); run status {{status}}.

# {{title}}

<!-- Case README template (the human page of cases/<name>/). `os-optics-kit contrib case` fills it and drops these
comments; by hand, replace every {{...}} value and delete the comments. Images come from the case's own run
(`os-optics view run --no-browser --snapshot images`): PNG, at most 400 KB each, at most 4 per case. -->

{{summary}}

{{images}}

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
{{results_rows}}

Verified with os-optics {{engine}} ({{catalog_text}}); run status {{status}}.

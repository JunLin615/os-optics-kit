## Summary

<!-- What changes and why. Link the issue it addresses, if any. -->

## Kind of change

- [ ] Case
- [ ] Tool or skill
- [ ] Documentation
- [ ] Repository, CI or packaging

## Checks

- [ ] Every commit is signed off (`git commit -s`).
- [ ] `python scripts/check_repo.py`, `ruff check .`, `mypy` and `python -m pytest` pass locally.
- [ ] Changed skills or cases: `os-optics-kit skills index --write` / `os-optics-kit cases index --write` were run
      and the indexes are committed.
- [ ] Changed cases: `os-optics-kit cases verify NAME` passes on the os-optics version and catalog release in
      `baseline.json`.
- [ ] Changed tutorials: the English and Simplified Chinese pages say the same thing.

<!-- Versions used for the checks above (os-optics-kit --version, os-optics --version, catalog release). -->

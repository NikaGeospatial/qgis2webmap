## What this changes

<!-- One or two sentences. -->

## Why

<!-- Link the issue or task from onlymap-js#29 this advances. -->

## Checks

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `python -m pytest tests/unit` passes
- [ ] `python scripts/package_plugin.py && python scripts/verify_package.py dist/*.zip` passes
- [ ] Installs via **Install from ZIP**, loads, and unloads cleanly
- [ ] No new network request from an exported artifact
- [ ] Any lossy transform is opt-in and reported in the fidelity report

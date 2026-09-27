# Tally Bridge

Frappe/ERPNext application for configurable ERPNext → TallyPrime synchronization and period-based Tally exports.

## Frappe installation

From the bench directory:

```bash
bench get-app https://github.com/<your-org>/tally_bridge
bench --site <site> install-app tally_bridge
```

`bench get-app` should place the app under `apps/tally_bridge` and add `tally_bridge` to `sites/apps.txt`.

## Development

Do not manually place the repository inside `apps/`. Keep this repository root as the app root containing `pyproject.toml`, `modules.txt`, `hooks.py`, and the `tally_bridge/` Python package.

The Windows `agent/` component is installed separately on the computer running TallyPrime.

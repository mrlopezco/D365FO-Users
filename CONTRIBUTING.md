# Contributing

Thank you for improving D365 F&O System Users.

## Branch workflow

- Day-to-day work: **`development`**
- Production-aligned releases: pull request **`development` → `main`**
- Merging into **`main`** triggers a dated GitHub Release (see [README](README.md)).

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\pip.exe install -r requirements-dev.txt
```

## Before you open a PR

```powershell
.\.venv\Scripts\ruff.exe check app
.\.venv\Scripts\python.exe -m app.main --help
```

Optional manual smoke (requires your gitignored config):

```powershell
.\.venv\Scripts\python.exe -m app.main --environment YOUR_ENV --input input\users-example.xlsx --dry-run --skip-preflight --yes
```

Set `DEBUG=1` in the environment to print a Python traceback for unexpected errors (see CLI behavior in `app/main.py`).

## Pull requests

- Keep changes focused; preserve CLI flags and Excel/YAML contracts unless the PR documents a breaking change.
- Do not commit `config/d365_environments.yaml`, `input/users.xlsx`, `.env`, or customer-specific data.
- CI runs **ruff** and **`python -m app.main --help`** on pull requests to `development` and `main`.

## Code style

- Python 3.10+; match existing module layout and typing style.
- Run `ruff check app` before pushing.

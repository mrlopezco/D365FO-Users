# FNO DMF Import Excel Generator

Generate Dynamics 365 Finance and Operations DMF import workbooks for **Employee V2** and **User information** from a simple input Excel of new users—or import the same data directly into F&O via OData.

## Setup

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

## Input

Edit [`input/users.xlsx`](input/users.xlsx) (sheet `Users`) with one row per user:

| Column | Maps to |
|--------|---------|
| `UserId` | User `USERID` (Employee `PERSONNELNUMBER` is left blank for F&O number sequence) |
| `Alias` | User `ALIAS` |
| `Email` | User `EMAIL` and Employee `PRIMARYCONTACTEMAIL` |
| `FirstName` | Employee `FIRSTNAME` (also builds display name) |
| `LastName` | Employee `LASTNAME` (also builds display name) |

Display name (`FirstName LastName`) fills User `USERNAME` and Employee `NAME` / `NAMEALIAS`.

All other DMF columns come from defaults in:

- [`config/employee_v2.yaml`](config/employee_v2.yaml) — OData entity `EmployeesV2`
- [`config/user_information.yaml`](config/user_information.yaml) — OData entity `SystemUsers`
- [`config/person_users.yaml`](config/person_users.yaml) — OData entity `PersonUsers` (user ↔ person link)

Change company, language, employment dates, and similar values in those YAML files without editing the script.

OData import order: **EmployeesV2** → **SystemUsers** → **PersonUsers** (links `UserId` to worker `PartyNumber`). Existing system users and duplicate links are treated as success when F&O reports they already exist.

## Run (interactive)

From the project root:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

You will be prompted to choose:

1. **Generate DMF Excel files** — same as before; writes to `output\YYYYMMDD_HHMMSS\`
2. **Import via OData** — choose a configured environment, then POST rows to F&O in order (Employee V2, then User information)

Skip the mode menu with `--mode file` or `--mode odata`.

### File mode (generate Excel only)

```powershell
.\.venv\Scripts\python.exe -m app.main --mode file --input input\users.xlsx --config-dir config --output-dir output
```

Output folder contains:

1. `Employee V2.xlsx`
2. `User information.xlsx`

### OData mode (import into F&O)

Copy [`config/d365_environments.example.yaml`](config/d365_environments.example.yaml) to `config/d365_environments.yaml` and fill in each environment (including `client_secret`). The local `d365_environments.yaml` file is **gitignored** and must not be committed.

**F&O / Entra prerequisites:**

1. Entra app registration with a client secret
2. API permission: **Microsoft Dynamics ERP** (with admin consent)
3. F&O: **System administration → Microsoft Entra applications** — map the client ID to a user
4. Security roles on that service account sufficient to create employees and system users

```powershell
# Test token and GET /data only
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF --test-connection

# Dry-run (no POST; prints sample payload)
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF --dry-run

# Import all rows from input/users.xlsx
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF
```

Optional flags:

- `--skip-person-link` — skip the PersonUsers step (no user–worker link)
- `--verbose` — print nested F&O error details and full OData JSON on failure
- `--stop-on-error` — stop after the first failed POST
- `--dry-run` — validate row building without calling F&O

OData POST uses the same column values as DMF generation, but JSON property names are taken from F&O (**PascalCase**), discovered via `$top=1` on each entity. By default only columns with a YAML **`source`**, plus columns marked **`odata_on_create: true`**, are sent (DMF default-only fields such as `PartyType` are skipped). Use **`odata_skip_create: true`** on sourced columns that F&O computes on insert (e.g. `Name`). Set **`odata_send_all_defaults: true`** on an entity YAML to revert to sending all non-empty columns (DMF-style). Empty fields are omitted. Override a property name with **`odata_property`** on a column if needed.

## Import into F&O (manual DMF or OData)

1. Import **Employee V2** first (`EmployeesV2`).
2. Import **User information** second (`SystemUsers`).
3. OData mode also posts **PersonUsers** to set the user’s Person (same as filling Person on the user form). DMF file mode still requires that link manually unless you import `Person users.xlsx` yourself.

Reference samples (Francisco demo row) are in [`DMF_Samples`](DMF_Samples).

# FNO DMF Import Excel Generator

Generate Dynamics 365 Finance and Operations DMF import workbooks for **Employee V2**, **User information**, and **security role assignments** from a simple input Excel—or import the same data directly into F&O via OData.

## Setup

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

## Input

Copy [`input/users-example.xlsx`](input/users-example.xlsx) to `input/users.xlsx` (the real file is **gitignored** and must not be committed). Edit `input/users.xlsx` (sheet `Users`) with one row per user:

| Column | Maps to |
|--------|---------|
| `UserId` | User `USERID` (Employee `PERSONNELNUMBER` is left blank for F&O number sequence) |
| `Alias` | User `ALIAS` |
| `Email` | User `EMAIL` and Employee `PRIMARYCONTACTEMAIL` |
| `FirstName` | Employee `FIRSTNAME` (also builds display name) |
| `LastName` | Employee `LASTNAME` (also builds display name) |
| `SecurityRoles` | Optional. Comma-separated F&O role display names → `SecurityUserRoleAssociations` |
| `SecurityLegalEntityIds` | Optional. Comma-separated legal entity codes (e.g. `1000`) → org assignment `OrganizationId` |
| `SecurityLegalEntities` | Optional. Comma-separated organization hierarchy types (e.g. `OPERATIONALLE`) → `HierarchyType` |

If either org column is filled, **both** must be filled. Leave all three security columns empty to onboard user/worker only (no role POSTs). Org scope rows are created for the Cartesian product of roles × legal entities × hierarchy types on that row.

Display name (`FirstName LastName`) fills User `USERNAME` and Employee `NAME` / `NAMEALIAS`.

All other DMF columns come from defaults in:

- [`config/employee_v2.yaml`](config/employee_v2.yaml) — OData entity `EmployeesV2`
- [`config/user_information.yaml`](config/user_information.yaml) — OData entity `SystemUsers`
- [`config/person_users.yaml`](config/person_users.yaml) — OData entity `PersonUsers` (user ↔ person link)
- [`config/security_user_role_association.yaml`](config/security_user_role_association.yaml) — OData `SecurityUserRoleAssociations`
- [`config/security_user_role_organization.yaml`](config/security_user_role_organization.yaml) — OData `SecurityUserRoleOrganizations`

Change company, language, employment dates, and similar values in those YAML files without editing the script.

OData import order: **EmployeesV2** → **SystemUsers** → **PersonUsers** → **SecurityUserRoleAssociations** → **SecurityUserRoleOrganizations** (when org columns are used). Role names are resolved to identifiers via GET `SecurityRoles`. Existing role/org assignments are skipped (add-only). Existing system users and duplicate person links are treated as success when F&O reports they already exist.

Try the template without touching your real file:

```powershell
.\.venv\Scripts\python.exe -m app.main --mode odata --environment CSCTEST02 --input input\users-example.xlsx --dry-run --skip-preflight --yes
```

## Run (interactive)

From the project root:

```powershell
.\.venv\Scripts\python.exe -m app.main
```

You will be prompted to choose:

1. **Generate DMF Excel files** — writes to `output\YYYYMMDD_HHMMSS\`
2. **Import via OData** — choose a configured environment, then POST rows to F&O

Skip the mode menu with `--mode file` or `--mode odata`.

### File mode (generate Excel only)

```powershell
.\.venv\Scripts\python.exe -m app.main --mode file --input input\users.xlsx --config-dir config --output-dir output
```

Output folder contains (when applicable):

1. `Employee V2.xlsx`
2. `User information.xlsx`
3. `Security user role association.xlsx` (if any `SecurityRoles` values)
4. `SystemSecurityUserRoleOrganizationEntity.xlsx` (if org columns are filled)

### OData mode (import into F&O)

Copy [`config/d365_environments.example.yaml`](config/d365_environments.example.yaml) to `config/d365_environments.yaml` and fill in each environment (including `client_secret`). The local `d365_environments.yaml` file is **gitignored** and must not be committed.

**F&O / Entra prerequisites:**

1. Entra app registration with a client secret
2. API permission: **Microsoft Dynamics ERP** (with admin consent)
3. F&O: **System administration → Microsoft Entra applications** — map the client ID to a user
4. Security roles on that service account sufficient to create employees, system users, and assign security roles

```powershell
# Test token and GET /data only
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF --test-connection

# Dry-run (no POST; prints sample payloads)
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF --dry-run --input input\users-example.xlsx

# Import all rows from input/users.xlsx
.\.venv\Scripts\python.exe -m app.main --mode odata --environment TESTUSMF
```

Optional flags:

- `--skip-person-link` — skip the PersonUsers step (no user–worker link)
- `--skip-security` — skip security role and organization assignment
- `--skip-security-orgs` — assign roles only; skip organization scope
- `--yes` — do not ask for confirmation after preflight finds duplicates
- `--skip-preflight` — skip environment duplicate checks before import
- `--verbose` — print nested F&O error details and full OData JSON on failure
- `--stop-on-error` — stop after the first failed POST
- `--dry-run` — validate row building without calling F&O POST (still connects for schema/role discovery when `client_secret` is set)

OData POST uses the same column values as DMF generation, but JSON property names are taken from F&O (**PascalCase**), discovered via `$top=1` on each entity. By default only columns with a YAML **`source`**, plus columns marked **`odata_on_create: true`**, are sent. Empty fields are omitted. Override a property name with **`odata_property`** on a column if needed.

## Import into F&O (manual DMF or OData)

1. Import **Employee V2** first (`EmployeesV2`).
2. Import **User information** second (`SystemUsers`).
3. Link user to worker via **Person users** (OData mode posts `PersonUsers` automatically unless `--skip-person-link`).
4. Import **Security user role association** (`SecurityUserRoleAssociations`).
5. Import **SystemSecurityUserRoleOrganizationEntity** when org columns were used (`SecurityUserRoleOrganizations`).

Reference samples (Francisco demo row) are in [`DMF_Samples`](DMF_Samples).

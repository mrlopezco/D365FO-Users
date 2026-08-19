# Security Policy

## Supported versions

Security fixes are applied on the **`main`** branch and released via dated tags when changes are merged through pull requests.

## Reporting a vulnerability

**Do not** open a public GitHub issue for security vulnerabilities.

- Prefer [GitHub Security Advisories](https://github.com/mrlopezco/D365FO-Users/security/advisories) (Report a vulnerability) if you have access.
- Otherwise contact the repository maintainer privately with a description, steps to reproduce, and impact.

Please **do not** include real `client_secret` values, production `d365_environments.yaml` contents, or full `input/users.xlsx` files in reports.

## Secrets and logs

- Store credentials only in gitignored [`config/d365_environments.yaml`](config/d365_environments.yaml) (see [`config/d365_environments.example.yaml`](config/d365_environments.example.yaml)).
- When sharing CLI output, redact tokens and avoid pasting `--verbose` OData error bodies publicly—they may contain user identifiers or email addresses from your F&O environment.

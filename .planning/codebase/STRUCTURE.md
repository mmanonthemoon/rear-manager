# Structure

## Directory Layout

```
rear-manager/
├── app.py                        # Entire application (4,242 lines)
├── requirements.txt              # Python dependencies
├── install.sh                    # Deployment script
├── prepare_offline_packages.sh   # Offline package prep
├── README.md                     # Project documentation
├── rear_manager.db               # SQLite database (runtime, gitignored)
├── static/
│   └── favicon.svg
├── templates/                    # Jinja2 HTML templates (27 files)
│   ├── base.html                 # Base layout with nav
│   ├── login.html
│   ├── dashboard.html
│   ├── servers.html
│   ├── server_detail.html
│   ├── server_form.html
│   ├── server_bulk.html
│   ├── configure.html
│   ├── jobs.html
│   ├── job_detail.html
│   ├── settings.html
│   ├── users.html
│   ├── user_form.html
│   ├── change_password.html
│   ├── ansible_dashboard.html
│   ├── ansible_hosts.html
│   ├── ansible_host_form.html
│   ├── ansible_host_bulk.html
│   ├── ansible_groups.html
│   ├── ansible_playbooks.html
│   ├── ansible_playbook_editor.html
│   ├── ansible_run_form.html
│   ├── ansible_run_detail.html
│   ├── ansible_runs.html
│   ├── ansible_roles.html
│   └── ansible_role_editor.html
└── ansible/                      # Ansible workspace
    ├── ansible.cfg
    ├── inventories/              # Dynamic inventory (generated at runtime)
    ├── playbooks/                # Playbook YAML files (synced from DB)
    ├── roles/                    # Role directories (synced from DB)
    ├── group_vars/
    └── host_vars/
```

## Key File Locations

| File | Purpose |
|------|---------|
| `app.py` | Everything: routes, models, SSH, scheduling, auth |
| `rear_manager.db` | SQLite database — created on first run |
| `~/.ssh/rear_manager_rsa` | SSH key pair for server connections |
| `ansible/inventories/` | Runtime-generated inventory YAML files |
| `ansible/playbooks/` | Playbooks synced from DB on save |
| `ansible/roles/` | Roles synced from DB on save |

## app.py Internal Organization

| Lines | Section |
|-------|---------|
| 1–83 | Imports, constants, app init, global state |
| 84–172 | Utility functions (cron describe, safe dirname, filters) |
| 173–563 | Database: schema, init, migrations, Ansible workspace init |
| 564–745 | Settings, local IP, NFS target helpers |
| 746–785 | Auth helpers (local + AD), decorators |
| 786–832 | Offline package status |
| 833–1387 | SSH layer (build client, exec, test, OS info, upload) |
| 1388–1804 | Job system (create, track, run, scheduler) |
| 1806–2813 | ReaR routes (login, servers, jobs, schedules, settings) |
| 2814–3021 | User management + API status endpoints |
| 3022–3343 | Ansible core (inventory gen, disk sync, run execution) |
| 3344–4242 | Ansible routes (hosts, groups, playbooks, roles, runs) |

## Database Tables

| Table | Purpose |
|-------|---------|
| `users` | Local user accounts |
| `servers` | Managed servers with SSH credentials |
| `schedules` | Cron-based backup schedules per server |
| `backup_jobs` | Backup job history and logs |
| `settings` | Key-value application settings |
| `ansible_groups` | Ansible inventory groups |
| `ansible_hosts` | Ansible managed hosts |
| `ansible_host_groups` | Many-to-many host↔group mapping |
| `ansible_playbooks` | Playbook definitions (stored in DB + synced to disk) |
| `ansible_runs` | Ansible execution history and logs |
| `ansible_roles` | Role metadata |
| `ansible_role_files` | Role file contents (stored in DB + synced to disk) |

## Naming Conventions

- Routes: `snake_case` function names matching resource + action (e.g., `server_add`, `schedule_toggle`)
- Templates: `resource_action.html` pattern (e.g., `server_form.html`, `ansible_run_detail.html`)
- Internal helpers: `_underscore_prefix` for non-route functions
- Background job functions: `_run_*` or `_do_*` prefix
- API endpoints: `/api/` prefix, return JSON
- Constants: `UPPER_SNAKE_CASE`, defined at top of file
- Turkish comments/strings throughout (project is Turkish-language)

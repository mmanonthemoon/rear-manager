# External Integrations

**Analysis Date:** 2026-03-17

## APIs & External Services

**SSH (Paramiko):**
- Target Linux servers via SSH for ReaR backup operations and command execution
  - SDK/Client: `paramiko` 3.0+
  - Auth: SSH password or private key (`~/.ssh/rear_manager_rsa`)
  - Connection pooling via Ansible ControlMaster/ControlPersist
  - Ports: 22 (default, configurable per server)
  - Functions: `build_ssh_client()`, `ssh_exec_stream()` in `app.py`

**Ansible (subprocess):**
- Executed via `subprocess.Popen()` to run playbooks and inventory
  - SDK/Client: Ansible CLI (`ansible-playbook`, `ansible`)
  - Inventory: Dynamic YAML generated from `ansible_hosts` table → `/ansible/inventories/hosts.yml`
  - Config: `/ansible/ansible.cfg` (SSH pipelining, ControlMaster, 10 forks, 30s timeout)
  - Execution: Background process with 2-second polling for output capture
  - Environment: Inherits Flask process environment, working directory `/ansible/`

**Windows Remote Management (pywinrm + Ansible):**
- WinRM protocol for Windows Ansible hosts
  - SDK/Client: `pywinrm` 0.4+ (optional)
  - Connection: HTTP/HTTPS on port 5985/5986 (configurable per host)
  - Auth schemes: NTLM (domain), Kerberos, Basic (workgroup)
  - Ansible variables: `ansible_winrm_transport`, `ansible_winrm_scheme`, `ansible_winrm_server_cert_validation`
  - Usage: Ansible playbook execution against `win_*` tasks on Windows hosts

**ReaR (Relax-and-Recover):**
- Bare-metal backup tool installed on target servers
  - Installed via: SSH command execution (`rear mkbackup`)
  - Installation method:
    - Online: `apt-get install rear nfs-common genisoimage xorriso` (or distro equivalent)
    - Offline: SFTP .deb packages from `/offline-packages/<codename>/` then `dpkg -i`
  - Configuration: Generated `/etc/rear/local.conf` via SSH
  - Backup output: ISO + NETFS tar.gz archived to NFS mount
  - Functions: `ssh_install_rear()`, `ssh_configure_rear()`, `ssh_mkbackup_stream()`

**NFS (Network File System):**
- Centralized backup storage, manually configured on central server
  - Export location: `/srv/rear-backups` (configurable in settings)
  - Target servers mount: `nfs://<central_ip><nfs_export_path>/<hostname>`
  - Naming: Hostnames sanitized via `_safe_dirname()` (dots/special chars → dashes)
  - Functions: `get_nfs_target()` generates backup URL
  - Note: App does NOT configure NFS; Linux admin must run `nfs-kernel-server` setup

## Data Storage

**Databases:**
- SQLite 3.x (single file `rear_manager.db`)
  - Connection: `sqlite3.connect(DB_PATH)` with WAL mode
  - Row factory: `sqlite3.Row` for dict-like access
  - Tables: 14 tables (users, servers, schedules, backup_jobs, settings, ansible_*)

**File Storage:**
- Local filesystem only
  - Backup directory: `/srv/rear-backups/` (NFS mount expected for production)
  - Offline packages: `/offline-packages/<codename>/` (Ubuntu-specific .deb files)
  - SSH keys: `~/.ssh/rear_manager_rsa` (auto-generated, 4096-bit RSA)
  - Ansible workspace: `/ansible/` (playbooks, roles, inventory, group_vars, host_vars)
  - Template cache: None (Jinja2 renders fresh each request)

**Caching:**
- Ansible fact caching: Memory backend (cleared per playbook run)
- Flask session: In-memory, lost on restart (no persistent session store)
- No external caching layer (Redis, Memcached)

## Authentication & Identity

**Auth Provider:**
- Dual-mode (Local + Active Directory/LDAP)
  - Local: Username/password stored in `users` table (bcrypt or SHA256 hash)
  - Active Directory: LDAP3 integration for enterprise environments

**Local Authentication:**
- Built-in admin: `admin` / `admin123` (must change on first login)
- User registration: Manual via web UI
- Password hashing: Werkzeug `generate_password_hash()` (bcrypt if available, else SHA256)
- Functions: Standard session-based login in Flask (`session['user_id']`)

**Active Directory / LDAP Integration:**
- SDK/Client: `ldap3` 2.9+ (optional, required for AD auth)
- Configuration (in `settings` table):
  - `ad_server` - LDAP server hostname/IP
  - `ad_port` - LDAP port (389 for standard, 636 for LDAPS)
  - `ad_domain` - Domain name for display/filtering
  - `ad_base_dn` - Base DN for user search (e.g., `DC=corp,DC=local`)
  - `ad_bind_user` - Service account for LDAP bind
  - `ad_bind_pass` - Service account password
  - `ad_user_filter` - LDAP filter for user lookup (e.g., `(sAMAccountName={username})`)
  - `ad_admin_group` - AD group DN for admin role
  - `ad_user_group` - AD group DN for user role
- Functions: `authenticate_ad_user()` in `app.py`
- Usage: AD users auto-created in DB on successful auth, password validation via LDAP bind

**Server Authentication:**
- SSH credentials stored in `servers` table:
  - `ssh_password` - SSH login password (plaintext, encrypted at rest not implemented)
  - `ssh_key_path` - Path to private key file
  - `become_method` - Privilege escalation (none/sudo/su)
  - `become_password` - Sudo/su password (plaintext)
  - `become_same_pass` - Boolean: reuse SSH password for become
- Ansible host credentials in `ansible_hosts` table:
  - `ansible_user` - Connection username
  - `ansible_pass` - Connection password (plaintext)
  - `auth_type` - 'password' or 'key'
  - `ssh_key_path` - Path to SSH private key
  - `become_method` - Linux privilege escalation
  - `become_pass` - Sudo/su password

## Monitoring & Observability

**Error Tracking:**
- None (no external error tracking service)
- Application errors logged via `log()` callbacks to `backup_jobs.log_output` or `ansible_runs.output`

**Logs:**
- File: systemd journal via `journalctl -u rear-manager`
- Database: Stored in job/run records
  - Backup logs: `backup_jobs.log_output` (full command output)
  - Ansible logs: `ansible_runs.output` (playbook execution output)
  - Job status: `backup_jobs.status` (pending/running/success/failed)
  - Timestamps: `backup_jobs.started_at`, `backup_jobs.finished_at`
  - Job type: `backup_jobs.job_type` (backup/configure/install)

**Health Checks:**
- Ansible version check: `ansible --version` (on startup, optional warning if missing)
- APScheduler status: HAS_SCHEDULER flag, optional warning if unavailable
- LDAP/AD module: HAS_LDAP flag, optional warning if ldap3 not installed
- Paramiko module: HAS_PARAMIKO flag, required for SSH operations
- Offline package status: UI displays availability of .deb files per Ubuntu codename

## CI/CD & Deployment

**Hosting:**
- Single server, systemd service
  - Service unit: `/etc/systemd/system/rear-manager.service`
  - User: root
  - WorkingDirectory: `/opt/rear-manager`
  - ExecStart: `/opt/rear-manager/venv/bin/python3 app.py`
  - Restart: always, 5-second restart delay

**CI Pipeline:**
- None (no GitHub Actions, GitLab CI, or equivalent)
- Manual deployment via `install.sh` script
  - Creates directories, installs dependencies, generates SSH keys, starts service

**Containerization:**
- No Docker/Kubernetes support
- Designed for bare-metal Linux installation

## Environment Configuration

**Required env vars:**
- None (all configuration in SQLite `settings` table)
- Flask runs with environment variables inherited from systemd unit (PYTHONUNBUFFERED=1)

**Secrets location:**
- SQLite `settings` table: `ad_bind_pass` (plaintext)
- SQLite `servers` table: `ssh_password`, `become_password` (plaintext)
- SQLite `ansible_hosts` table: `ansible_pass`, `become_pass` (plaintext)
- Filesystem: `~/.ssh/rear_manager_rsa` (SSH private key, generated on setup)

**Note:** All sensitive data (passwords, LDAP bind password) stored in plaintext in SQLite. No encryption at rest.

## Webhooks & Callbacks

**Incoming:**
- None (no webhook receivers)

**Outgoing:**
- None (no external notifications or callbacks)
- Notifications: UI polling only (2-second AJAX polls for job status)

## External System Dependencies

**Linux Target Servers:**
- SSH service on port 22 (or custom)
- ReaR package (installed via app)
- NFS client (installed with ReaR)
- sudo/su for privilege escalation (optional, if not root)

**Windows Target Servers:**
- WinRM service on port 5985/5986
- PowerShell (for Ansible modules)
- Admin credentials for execution

**Network:**
- NFS server (external, manually configured on central server)
- DNS (optional, for hostname resolution)
- No internet required (fully offline-capable)

## Version Lock / Dependency Pinning

- Minimum versions specified in `requirements.txt`:
  - flask>=2.3.0
  - paramiko>=3.0.0
  - apscheduler>=3.10.0
  - werkzeug>=2.3.0
  - ldap3>=2.9.0 (optional)
  - pywinrm>=0.4.3 (optional)
- No upper bounds, allows newer versions
- No lockfile (e.g., `poetry.lock`, `Pipfile.lock`) to pin exact versions

---

*Integration audit: 2026-03-17*

# Technology Stack

**Analysis Date:** 2026-03-17

## Languages

**Primary:**
- Python 3.8+ - Main application language, all backend logic and business operations

**Secondary:**
- HTML5 - Template rendering via Jinja2
- CSS3 - Inline styling in base template
- JavaScript (ES6+) - Client-side form handling, AJAX polling, modal management
- YAML - Ansible playbooks, inventory, and role definitions
- Shell Script - Installation automation (`install.sh`, `prepare_offline_packages.sh`)

## Runtime

**Environment:**
- Python 3.8+ (tested on Ubuntu 20.04+, RHEL 8+, Debian 11+)
- Optional: Python virtual environment (`venv`) for dependency isolation
- systemd service for process management

**Package Manager:**
- pip (Python Package Index)
- Lockfile: Not used (requirements.txt without pinned versions beyond minimum)

## Frameworks

**Core:**
- Flask 2.3+ - Web framework, HTTP routing, request handling
- Jinja2 3.x - Server-side template engine (included with Flask)
- Werkzeug 2.3+ - WSGI application utilities, password hashing

**Task Scheduling:**
- APScheduler 3.10+ - Background job scheduling for cron-like backup automation

**Testing:**
- No dedicated test framework found (production code only)

**Build/Dev:**
- Ansible (system package or pip) - Infrastructure orchestration and execution

## Key Dependencies

**Critical:**
- Paramiko 3.0+ - SSH client library for remote command execution, SFTP file transfer, key management
- ldap3 2.9+ (optional) - LDAP/Active Directory authentication and user validation
- APScheduler 3.10+ - Cron-like job scheduling for automated backups
- Werkzeug 2.3+ - Password hashing (bcrypt fallback to SHA256), HTTP utilities
- pywinrm 0.4+ (optional) - Windows Remote Management for Ansible host connectivity testing

**Infrastructure:**
- sqlite3 (built-in) - Relational database, no server required
- Ansible (system or pip) - Playbook execution, inventory management, host orchestration

## Configuration

**Environment:**
- Configuration stored in SQLite `settings` table (key-value pairs)
- No `.env` file required; all settings persist in database
- Critical settings:
  - `central_ip` - Backup server IP for NFS target construction
  - `nfs_export_path` - Directory path for backup storage (`/srv/rear-backups` default)
  - `ssh_key_path` - Path to SSH private key (`~/.ssh/rear_manager_rsa` default)
  - `ad_enabled` - Active Directory/LDAP authentication toggle
  - AD connection details (server, port, domain, base DN, bind credentials)

**Build:**
- `ansible.cfg` - Ansible configuration at `/ansible/ansible.cfg`
  - Inventory path: `/ansible/inventories/hosts.yml` (auto-generated from DB)
  - Roles path: `/ansible/roles`
  - SSH pipelining enabled, ControlMaster/ControlPersist for connection pooling
  - Fact caching via memory backend
  - 10 parallel forks, 30-second timeout

## Platform Requirements

**Development:**
- Python 3.8+ interpreter
- pip for package installation
- SSH client (paramiko handles this internally)
- System packages: libldap2-dev, libsasl2-dev, libssl-dev (for ldap3 compilation)

**Production:**
- Ubuntu 20.04 LTS+ / Debian 11+ / RHEL 8+ / AlmaLinux / Rocky Linux
- Minimum: 1 CPU, 512 MB RAM, 10 GB disk
- Recommended: 2+ CPU, 2 GB RAM, 50+ GB disk (for backup storage)
- Network: SSH access to target servers (port 22 or custom), optional WinRM for Windows (5985/5986)
- NFS kernel server or SMB share (configured externally, not by app)

## Application Entry Points

- **Main application:** `app.py` (4500+ lines, single-file monolithic design)
- **Database location:** `rear_manager.db` (SQLite, auto-created)
- **Web binding:** `0.0.0.0:80` HTTP (no HTTPS)
- **Backup storage root:** `/srv/rear-backups` (configurable)
- **SSH key storage:** `~/.ssh/rear_manager_rsa` (generated on setup)
- **Ansible workspace:** `/ansible/` subdirectory (includes playbooks, roles, inventory)
- **Offline packages:** `/offline-packages/` subdirectory organized by Ubuntu codename

## Database

**Engine:** SQLite 3.x
**Location:** `rear_manager.db` (single file, WAL mode enabled for concurrency)
**Tables (14 total):**
- `users` - User accounts, roles (admin/user), auth type (local/AD)
- `servers` - ReaR backup targets with SSH/become credentials
- `schedules` - Cron-like backup schedules
- `backup_jobs` - Backup execution history and logs
- `settings` - Application configuration (key-value)
- `ansible_groups` - Host groups (hierarchical)
- `ansible_hosts` - Ansible-managed targets (Linux/Windows with connection config)
- `ansible_host_groups` - Host-group associations
- `ansible_playbooks` - Playbook YAML content
- `ansible_runs` - Playbook execution history
- `ansible_roles` - Ansible role definitions
- `ansible_role_files` - Role file sections (tasks/handlers/vars/defaults/meta/templates/files)

## Concurrency & Threading

- **Background scheduler:** APScheduler `BackgroundScheduler` runs in separate thread
- **Job locking:** `threading.Lock()` protects concurrent job execution
- **Session management:** Flask built-in session handling with random secret key
- **Database:** SQLite with WAL mode for WAL-aware concurrent readers

---

*Stack analysis: 2026-03-17*

"""ReaR service — config generation, OS detection, offline install, install/configure runners."""

import os
import re
import secrets
import datetime
import traceback
import subprocess

from flask import current_app

from config import OFFLINE_PKG_DIR, UBUNTU_CODENAMES, BACKUP_ROOT, KEY_PATH
from services import ssh as ssh_service
from services import jobs as job_service
from models import jobs as job_repo
from models import settings as settings_repo
from models import servers as server_repo


# ─────────────────────────────────────────────────────────────
# ÖZEL İSTİSNALAR
# ─────────────────────────────────────────────────────────────
class ReaRInstallError(Exception):
    """Raised when ReaR installation fails on the remote host."""


class ReaRConfigError(Exception):
    """Raised when ReaR configuration cannot be applied."""


# ─────────────────────────────────────────────────────────────
# OFFLİNE UBUNTU PAKET YÖNETİMİ
# ─────────────────────────────────────────────────────────────
def get_offline_pkg_status():
    """
    offline-packages/ dizinindeki mevcut paket setlerini döner.
    {codename: {'count': N, 'size': 'XM', 'meta': {...}, 'ready': True/False}}
    """
    import json as _json

    result = {}
    os.makedirs(OFFLINE_PKG_DIR, exist_ok=True)

    for codename in UBUNTU_CODENAMES:
        pkg_dir = os.path.join(OFFLINE_PKG_DIR, codename)
        if not os.path.isdir(pkg_dir):
            result[codename] = {'ready': False, 'count': 0, 'size': '0', 'meta': {}}
            continue

        debs = [f for f in os.listdir(pkg_dir) if f.endswith('.deb')]
        if not debs:
            result[codename] = {'ready': False, 'count': 0, 'size': '0', 'meta': {}}
            continue

        # Toplam boyut
        total_bytes = sum(
            os.path.getsize(os.path.join(pkg_dir, f)) for f in debs
        )
        size_mb = f"{total_bytes / 1024 / 1024:.1f} MB"

        # meta.json varsa oku
        meta = {}
        meta_path = os.path.join(pkg_dir, 'meta.json')
        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as mf:
                    meta = _json.load(mf)
            except Exception:
                pass

        result[codename] = {
            'ready':  True,
            'count':  len(debs),
            'size':   size_mb,
            'meta':   meta,
            'path':   pkg_dir,
        }

    return result


def get_ubuntu_codename_via_ssh(server):
    """
    SSH ile hedef sunucunun Ubuntu codename'ini alır.
    Döner: (codename_str | None, version_str | None)
    """
    try:
        client = ssh_service.build_ssh_client(server)
        _, stdout, _ = client.exec_command(
            'lsb_release -cs 2>/dev/null; lsb_release -rs 2>/dev/null',
            timeout=10
        )
        out = stdout.read().decode().strip().split('\n')
        client.close()
        codename = out[0].strip().lower() if out else None
        version  = out[1].strip() if len(out) > 1 else None
        return codename, version
    except Exception:
        return None, None


def ssh_install_offline_ubuntu(server_dict, job_id):
    """
    Offline Ubuntu kurulumu:
    1. SSH ile codename tespit et
    2. offline-packages/<codename>/ dizinindeki .deb'leri tar.gz yap
    3. Hedef sunucuya SFTP ile gönder
    4. become ile: dpkg -i (iki pass) + apt-get install -f (yerel çözümleme)
    5. Geçici dosyaları temizle

    Döner: (success: bool, message: str)
    """
    log = lambda t: job_service._append_log(job_id, t)

    # ── Codename tespit ─────────────────────────────────────
    log("► Ubuntu sürümü tespit ediliyor...")
    codename, version = get_ubuntu_codename_via_ssh(server_dict)

    if not codename:
        return False, "Ubuntu sürümü tespit edilemedi."

    log(f"► Tespit edildi: Ubuntu {version or '?'} ({codename})")

    # ── Offline paket var mı? ───────────────────────────────
    pkg_dir = os.path.join(OFFLINE_PKG_DIR, codename)
    debs = []
    if os.path.isdir(pkg_dir):
        debs = [f for f in os.listdir(pkg_dir) if f.endswith('.deb')]

    if not debs:
        msg = (
            f"Ubuntu '{codename}' için offline paket bulunamadı: {pkg_dir}\n"
            f"Lütfen internet erişimi olan bir makinede önce "
            f"'prepare_offline_packages.sh' betiğini çalıştırın."
        )
        return False, msg

    log(f"► {len(debs)} adet .deb paketi bulundu ({pkg_dir})")

    # ── Paketleri tar.gz'e sıkıştır ────────────────────────
    import tarfile
    import tempfile
    tmp_tar = tempfile.mktemp(suffix='.tar.gz', prefix='rear_pkgs_')
    log(f"► Paketler arşivleniyor ({len(debs)} dosya)...")

    try:
        with tarfile.open(tmp_tar, 'w:gz') as tar:
            for deb in sorted(debs):
                tar.add(os.path.join(pkg_dir, deb), arcname=deb)
        tar_size_mb = os.path.getsize(tmp_tar) / 1024 / 1024
        log(f"► Arşiv boyutu: {tar_size_mb:.1f} MB")
    except Exception as e:
        return False, f"Arşivleme hatası: {e}"

    # ── Hedef sunucuya gönder ───────────────────────────────
    remote_tmp_dir = f"/tmp/.rear_pkgs_{secrets.token_hex(4)}"
    remote_tar     = f"{remote_tmp_dir}.tar.gz"

    log(f"► Paketler hedef sunucuya kopyalanıyor...")
    log(f"  Hedef: {server_dict['ip_address']}:{remote_tar}")

    try:
        client = ssh_service.build_ssh_client(server_dict)
        sftp   = client.open_sftp()

        # İlerleme callback
        total = os.path.getsize(tmp_tar)

        def progress(transferred, total_size):
            pct = int(transferred / total_size * 100)
            if pct % 20 == 0 or transferred == total_size:
                mb = transferred / 1024 / 1024
                log(f"  ↑ {mb:.1f} MB / {total_size/1024/1024:.1f} MB ({pct}%)")

        sftp.put(tmp_tar, remote_tar, callback=progress)
        sftp.close()
        client.close()
        log("► Kopyalama tamamlandı ✓")
    except Exception as e:
        try:
            os.unlink(tmp_tar)
        except Exception:
            pass
        return False, f"SFTP gönderme hatası: {e}"
    finally:
        try:
            os.unlink(tmp_tar)
        except Exception:
            pass

    # ── Hedefte: aç + kur + temizle ────────────────────────
    log("► Paketler açılıyor ve kuruluyor...")
    log("  (dpkg -i ile offline kurulum — internet gerekmez)")

    # Tek komut bloğu: mkdir, tar xz, dpkg, dpkg (2. pass), temizlik
    # DEBIAN_FRONTEND=noninteractive: debconf interaktif prompt'larını engeller
    # --force-confdef --force-confnew: mevcut config dosyaları için soru sormaz
    install_script = f"""
export DEBIAN_FRONTEND=noninteractive
export DEBCONF_NONINTERACTIVE_SEEN=true
mkdir -p {remote_tmp_dir}
echo "[1/4] Arşiv açılıyor..."
tar xzf {remote_tar} -C {remote_tmp_dir}/ || exit 1
echo "[2/4] dpkg ile kuruluyor (1. geçiş)..."
DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confnew -i {remote_tmp_dir}/*.deb 2>&1 || true
echo "[3/4] dpkg ikinci geçiş (bağımlılık sırası)..."
DEBIAN_FRONTEND=noninteractive dpkg --force-confdef --force-confnew -i {remote_tmp_dir}/*.deb 2>&1 || true
echo "[4/4] Bağımlılıklar düzeltiliyor..."
DEBIAN_FRONTEND=noninteractive apt-get install -f -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confnew" --no-install-recommends 2>&1 || true
echo "Temizleniyor..."
rm -rf {remote_tmp_dir} {remote_tar}
echo "KURULUM_TAMAM"
"""

    ec, out = ssh_service.ssh_exec_stream(server_dict, install_script.strip(), log)

    # KURULUM_TAMAM kontrolü (dpkg exit code'u güvenilmez olabilir)
    if 'KURULUM_TAMAM' in out:
        return True, "Offline kurulum başarılı."
    elif ec == 0:
        return True, "Offline kurulum tamamlandı."
    else:
        # dpkg -i bazı hatalara rağmen 0 dışı dönebilir; rear kuruldu mu kontrol et
        ec2, ver = ssh_service.ssh_exec_stream(server_dict, 'rear --version 2>/dev/null', lambda x: None)
        if ec2 == 0 and 'Relax-and-Recover' in ver:
            log(f"► ReaR kurulmuş: {ver.strip()}")
            return True, f"ReaR kuruldu (uyarılarla): {ver.strip()}"
        return False, f"Kurulum başarısız (kod: {ec})."


# ─────────────────────────────────────────────────────────────
# REAR YAPILANDIRMA ÜRETECİ
# ─────────────────────────────────────────────────────────────
def generate_rear_config(server, cfg, extra_server_exclude=''):
    """
    ReaR local.conf içeriğini üretir.
    server: dict
    cfg: settings dict
    extra_server_exclude: sunucuya özel ek hariç dizinler (multiline str)
    """
    from models import settings as settings_repo

    backup_url  = settings_repo.get_nfs_target(server['hostname'], _get_local_ip, _safe_dirname, BACKUP_ROOT)
    autoresize  = cfg.get('autoresize', '1')
    migration   = cfg.get('migration_mode', '1')
    output_type = cfg.get('rear_output', 'ISO')
    backup_type = cfg.get('rear_backup', 'NETFS')

    # Hariç tutulacak dizinleri birleştir
    global_excl = cfg.get('global_exclude_dirs', '')
    server_excl = server.get('exclude_dirs', '') or ''
    if extra_server_exclude:
        server_excl = (server_excl + '\n' + extra_server_exclude).strip()

    all_excludes = []
    for src in [global_excl, server_excl]:
        for line in src.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                all_excludes.append(line)

    lines = [
        "# ReaR Yapılandırması - ReaR Manager v2.0 tarafından oluşturuldu",
        f"# Sunucu  : {server['hostname']}",
        f"# Tarih   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"OUTPUT={output_type}",
        f"BACKUP={backup_type}",
        f'BACKUP_URL="{backup_url}"',
        "",
        "# ── Farklı donanım / disk boyutu ───────────────────────",
    ]

    if migration == '1':
        lines.append("MIGRATION_MODE=true")
    else:
        lines.append("# MIGRATION_MODE=true")

    if autoresize == '1':
        lines += [
            'AUTORESIZE_PARTITIONS=("true")',
            'AUTORESIZE_EXCLUDE_PARTITIONS=()',
            "AUTOSHRINK_DISK_SIZE_LIMIT_PERCENTAGE=80",
            "AUTOINCREASE_DISK_SIZE_THRESHOLD_PERCENTAGE=10",
        ]

    lines += [
        "",
        "# ── Ağ ────────────────────────────────────────────────",
        "USE_DHCLIENT=yes",
        'NETWORKING_PREPARATION_COMMANDS=("ip link set dev eth0 up" "dhclient eth0")',
        "",
        "# ── Hariç tutulan yollar ───────────────────────────────",
        "BACKUP_PROG_EXCLUDE=(",
        "    '${BACKUP_PROG_EXCLUDE[@]}'",
        "    '/tmp/*'",
        "    '/var/tmp/*'",
        "    '/proc/*'",
        "    '/sys/*'",
        "    '/dev/*'",
        "    '/run/*'",
    ]

    for excl in all_excludes:
        lines.append(f"    '{excl}'")

    lines += [
        ")",
        "",
        "# ── ISO / Kurtarma ayarları ────────────────────────────",
        "OUTPUT_URL=''",
        "ISO_DEFAULT=automatic",
        "",
        "# ── Loglama ────────────────────────────────────────────",
        "KEEP_BUILD_DIR=no",
        "REAR_PROGNAME=rear",
    ]

    return '\n'.join(lines) + '\n'


# ─────────────────────────────────────────────────────────────
# ARKA PLAN İŞ FONKSİYONLARI (background thread functions)
# ─────────────────────────────────────────────────────────────
def _run_install_rear(job_id, server_dict):
    """Install ReaR on a remote server (background thread function)."""
    log = lambda t: job_service._append_log(job_id, t)
    job_service._set_job_status(job_id, 'running')
    job_repo.set_started(job_id)

    log("=== ReaR Kurulumu Başlıyor ===")
    log("► OS bilgisi alınıyor...")
    os_info = ssh_service.ssh_get_os_info(server_dict)
    log(os_info)

    os_lower  = os_info.lower()
    is_ubuntu = 'ubuntu' in os_lower
    is_debian = 'debian' in os_lower and not is_ubuntu
    is_redhat = any(x in os_lower for x in ['rhel', 'centos', 'almalinux', 'rocky', 'fedora'])
    is_suse   = any(x in os_lower for x in ['suse', 'sles'])

    installed = False   # kurulum başarılı mı?

    # ── UBUNTU ─────────────────────────────────────────────────────────────────
    if is_ubuntu:
        codename, version = get_ubuntu_codename_via_ssh(server_dict)
        log(f"► Hedef: Ubuntu {version or '?'} ({codename or 'bilinmiyor'})")
        log("")

        # ── 1. ADIM: apt-get ile dene (internet varsa hızlı çözüm) ──────────
        log("► [1/2] apt-get ile kurulum deneniyor...")
        apt_cmd = (
            'export DEBIAN_FRONTEND=noninteractive && '
            'export DEBCONF_NONINTERACTIVE_SEEN=true && '
            'apt-get update -q 2>&1 | tail -3 && '
            'apt-get install -y '
            '-o Dpkg::Options::="--force-confdef" '
            '-o Dpkg::Options::="--force-confnew" '
            'rear nfs-common genisoimage xorriso '
            'syslinux syslinux-common isolinux 2>&1'
        )
        ec_apt, _ = ssh_service.ssh_exec_stream(server_dict, apt_cmd, log)

        if ec_apt == 0:
            log("► apt-get kurulum başarılı ✓")
            installed = True
        else:
            log(f"► apt-get başarısız (kod: {ec_apt}) — offline pakete geçiliyor...")
            log("")

            # ── 2. ADIM: offline paket ───────────────────────────────────────
            log("► [2/2] Offline paket kurulumu deneniyor...")
            pkg_status = get_offline_pkg_status()
            has_offline = (
                codename and
                codename in pkg_status and
                pkg_status[codename].get('ready', False)
            )

            if has_offline:
                pkg_info = pkg_status[codename]
                log(f"► Offline paket seti hazır: {pkg_info['count']} paket, {pkg_info['size']}")
                ok, msg = ssh_install_offline_ubuntu(server_dict, job_id)
                if ok:
                    log(f"► {msg}")
                    installed = True
                else:
                    log(f"[HATA] Offline kurulum başarısız: {msg}")
            else:
                if codename:
                    log(f"[HATA] Ubuntu '{codename}' için offline paket paketi bulunamadı.")
                    log(f"       Beklenen konum: {os.path.join(OFFLINE_PKG_DIR, codename)}/")
                else:
                    log("[HATA] Ubuntu codename tespit edilemedi.")
                log("")
                log("ÇÖZÜM: İnternet bağlantısı olan bir Ubuntu makinesinde şunu çalıştırın:")
                log(f"  sudo bash prepare_offline_packages.sh")
                log(f"Sonra dosyaları bu sunucuya kopyalayın:")
                log(f"  rsync -avz /opt/rear-manager/offline-packages/ \\")
                log(f"      root@<bu_sunucu>:/opt/rear-manager/offline-packages/")

        if not installed:
            job_service._set_job_status(job_id, 'failed')
            return

    # ── DİĞER DAĞITIMLAR ──────────────────────────────────────────────────────
    elif is_debian:
        log("► Debian tespit edildi — apt-get ile kurulum...")
        ec, _ = ssh_service.ssh_exec_stream(server_dict, (
            'export DEBIAN_FRONTEND=noninteractive && '
            'export DEBCONF_NONINTERACTIVE_SEEN=true && '
            'apt-get update -q && '
            'apt-get install -y '
            '-o Dpkg::Options::="--force-confdef" '
            '-o Dpkg::Options::="--force-confnew" '
            'rear nfs-common genisoimage xorriso syslinux syslinux-common'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            job_service._set_job_status(job_id, 'failed')
            return

    elif is_redhat:
        log("► RHEL/CentOS/Alma/Rocky tespit edildi — dnf ile kurulum...")
        ec, _ = ssh_service.ssh_exec_stream(server_dict, (
            'dnf install -y epel-release 2>/dev/null || true; '
            'dnf install -y rear nfs-utils genisoimage syslinux'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            job_service._set_job_status(job_id, 'failed')
            return

    elif is_suse:
        log("► SUSE tespit edildi — zypper ile kurulum...")
        ec, _ = ssh_service.ssh_exec_stream(
            server_dict, 'zypper install -y rear nfs-client genisoimage syslinux', log
        )
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            job_service._set_job_status(job_id, 'failed')
            return

    else:
        log("[UYARI] Bilinmeyen OS — apt-get ile deneniyor...")
        ec, _ = ssh_service.ssh_exec_stream(server_dict, (
            'export DEBIAN_FRONTEND=noninteractive && '
            'export DEBCONF_NONINTERACTIVE_SEEN=true && '
            'apt-get update -q && '
            'apt-get install -y '
            '-o Dpkg::Options::="--force-confdef" '
            '-o Dpkg::Options::="--force-confnew" '
            'rear nfs-common genisoimage xorriso || '
            '(dnf install -y epel-release 2>/dev/null; dnf install -y rear nfs-utils genisoimage)'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            job_service._set_job_status(job_id, 'failed')
            return

    # ── Sürüm doğrulama ───────────────────────────────────────────────────────
    log("")
    log("► ReaR sürümü doğrulanıyor...")
    _, ver = ssh_service.ssh_exec_stream(server_dict, 'rear --version 2>/dev/null', log)
    ver_str = ver.strip()
    if 'Relax-and-Recover' not in ver_str:
        log("[HATA] ReaR kurulu görünmüyor — 'rear --version' çalışmadı.")
        job_service._set_job_status(job_id, 'failed')
        return

    log(f"► ReaR Versiyonu: {ver_str}")
    log("")

    server_repo.update_rear_installed(server_dict['id'], os_info.split('\n')[0][:200])

    log("=== ReaR Kurulumu Tamamlandı ✓ ===")
    job_service._set_job_status(job_id, 'success')


def _run_configure_rear(job_id, server_dict, rear_config_content):
    """Configure ReaR on a remote server (background thread function)."""
    log = lambda t: job_service._append_log(job_id, t)
    job_service._set_job_status(job_id, 'running')
    job_repo.set_started(job_id)

    log("=== ReaR Yapılandırması Başlıyor ===")
    ssh_service.ssh_exec_stream(server_dict, 'mkdir -p /etc/rear', log)
    ssh_service.ssh_exec_stream(server_dict,
        'test -f /etc/rear/local.conf && '
        'cp /etc/rear/local.conf /etc/rear/local.conf.bak && '
        'echo "Eski config yedeklendi" || true', log)

    log("► Yapılandırma dosyası yazılıyor...")
    ok, msg = ssh_service.ssh_upload_file(server_dict, rear_config_content, '/etc/rear/local.conf')
    if not ok:
        log(f"[HATA] {msg}")
        job_service._set_job_status(job_id, 'failed')
        return

    log("► Doğrulanıyor...")
    ssh_service.ssh_exec_stream(server_dict, 'rear dump 2>&1 | head -20', log)

    server_repo.update_rear_configured(server_dict['id'])

    log("=== Yapılandırma Tamamlandı ✓ ===")
    job_service._set_job_status(job_id, 'success')


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _safe_dirname(hostname):
    """Convert hostname to a safe directory name."""
    return re.sub(r'[^a-zA-Z0-9_-]', '-', hostname).strip('-') or 'unknown'


def _get_local_ip():
    """Local IP helper (mirrors app.py _get_local_ip)."""
    import socket
    import subprocess as _sp
    try:
        r = _sp.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = r.stdout.strip().split()
        for ip in ips:
            if not ip.startswith('127.') and not ip.startswith('::1'):
                return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if not ip.startswith('127.'):
                return ip
    except Exception:
        pass
    return '127.0.0.1'

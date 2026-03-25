"""SSH service — all paramiko-based remote operations.

Receives server dicts from callers; does NOT touch the database.
"""

import os
import time
import socket
import re
import secrets
import shlex

import paramiko
from flask import current_app

from config import KEY_PATH
from models import settings as settings_repo


# ─────────────────────────────────────────────────────────────
# ÖZEL İSTİSNALAR
# ─────────────────────────────────────────────────────────────
class SSHConnectionError(Exception):
    """Raised when paramiko.connect() fails."""


class SSHAuthenticationError(SSHConnectionError):
    """Raised when SSH authentication fails."""


class SSHTimeoutError(SSHConnectionError):
    """Raised when sudo/su prompt times out."""


class SSHCommandError(Exception):
    """Raised when a remote command returns non-zero exit code."""


# ─────────────────────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────────────────────
def _get_become_password(server):
    """
    Become şifresini döner.
    become_same_pass=1 → SSH şifresi kullanılır
    become_same_pass=0 → become_password alanı kullanılır
    """
    same = server.get('become_same_pass', 1)
    if str(same) == '1':
        return server.get('ssh_password', '') or ''
    return server.get('become_password', '') or ''


def _wrap_become_cmd(server, command):
    """
    Komutu become yöntemine göre sarar.
    - none  : komut olduğu gibi çalışır
    - sudo  : sudo -H -u <user> bash -c '...'  (şifre PTY prompt ile gönderilir)
    - su    : su - <user> -c '...'             (şifre PTY prompt ile gönderilir)
    Döner: (wrapped_command, method, become_pass)
    """
    method = server.get('become_method', 'none')
    if method == 'none':
        return command, 'none', ''

    buser = (server.get('become_user', 'root') or 'root').strip()
    bpass = _get_become_password(server)

    if method == 'sudo':
        # PTY prompt yöntemi: sudo şifre isteyince prompt yakala, şifre gönder
        # -p 'SUDO_PASS_PROMPT: ' → sabit prompt metni ile yakalaması kolay
        # -H : HOME=/root,  -u : hedef kullanıcı
        if bpass:
            wrapped = (
                f"sudo -p 'SUDO_PASS_PROMPT: ' -H -u {buser} "
                f"bash -c {shlex.quote(command)}"
            )
        else:
            # NOPASSWD sudoers — şifre gönderme
            wrapped = f"sudo -H -u {buser} bash -c {shlex.quote(command)}"
        return wrapped, 'sudo', bpass

    elif method == 'su':
        wrapped = f"su - {buser} -c {shlex.quote(command)}"
        return wrapped, 'su', bpass

    return command, 'none', ''


# ─────────────────────────────────────────────────────────────
# SSH İSTEMCİSİ
# ─────────────────────────────────────────────────────────────
def build_ssh_client(server):
    """Paramiko SSH istemcisi oluşturur ve bağlanır.

    Raises:
        SSHAuthenticationError: Kimlik doğrulama başarısız.
        SSHConnectionError: Bağlantı veya ağ hatası.
        RuntimeError: paramiko kurulu değil.
    """
    if not _has_paramiko():
        raise RuntimeError(
            "paramiko modülü kurulu değil. 'pip install paramiko' komutunu çalıştırın."
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=server['ip_address'],
        port=int(server['ssh_port']),
        username=server['ssh_user'],
        timeout=30,
    )
    if server['ssh_auth'] == 'key':
        kp = settings_repo.get_settings().get('ssh_key_path', KEY_PATH)
        kwargs['key_filename'] = kp
    else:
        kwargs['password'] = server['ssh_password']
    try:
        client.connect(**kwargs)
    except paramiko.AuthenticationException as e:
        raise SSHAuthenticationError(
            f"Auth failed for {server['hostname']}: {e}"
        ) from e
    except paramiko.SSHException as e:
        raise SSHConnectionError(
            f"SSH error for {server['hostname']}: {e}"
        ) from e
    except (socket.timeout, OSError) as e:
        raise SSHConnectionError(
            f"Network error for {server['hostname']}: {e}"
        ) from e
    return client


def _has_paramiko():
    try:
        import paramiko as _p  # noqa: F401
        return True
    except ImportError:
        return False


# ─────────────────────────────────────────────────────────────
# SSH KOMUT ÇALIŞTIRICISI
# ─────────────────────────────────────────────────────────────
def ssh_exec_stream(server, command, log_cb):
    """
    SSH ile komut çalıştırır, PTY üzerinden çıktıyı satır satır log_cb'ye yollar.
    become (sudo/su) için PTY prompt beklenir ve şifre gönderilir.

    sudo: 'SUDO_PASS_PROMPT: ' sabit prompt metni ile şifre yakalaması güvenilir.
    su  : 'password:', 'parola:' vb. prompt ile şifre gönderilir.
    """
    wrapped_cmd, actual_method, bpass = _wrap_become_cmd(server, command)

    output_lines = []
    exit_code    = -1

    try:
        client    = build_ssh_client(server)
        transport = client.get_transport()
        chan      = transport.open_session()
        chan.get_pty(term='vt100', width=220, height=50)
        chan.exec_command(wrapped_cmd)

        prompt_deadline = time.monotonic() + 30  # 30s timeout for sudo/su prompt

        buf           = b''
        pass_sent     = False
        pass_attempts = 0
        MAX_ATTEMPTS  = 3

        # Prompt desenleri (küçük harf karşılaştırması için)
        SUDO_PROMPT = b'sudo_pass_prompt: '
        SU_PROMPTS  = (
            b'password:', b'parola:',
            'şifre:'.encode('utf-8'),
            b'mot de passe:', b'passwort:',
            b'password for',
        )

        while True:
            if chan.recv_ready():
                data = chan.recv(8192)
                if not data:
                    break
                buf += data

                buf_lower = buf.lower()

                # ── Sudo şifre promptu ──────────────────────────────
                if actual_method == 'sudo' and not pass_sent and pass_attempts < MAX_ATTEMPTS:
                    if SUDO_PROMPT in buf_lower:
                        if bpass:
                            chan.sendall((bpass + '\n').encode('utf-8'))
                        else:
                            chan.sendall(b'\n')
                        pass_sent     = True
                        pass_attempts += 1
                        continue

                # ── Su şifre promptu ────────────────────────────────
                if actual_method == 'su' and not pass_sent and pass_attempts < MAX_ATTEMPTS:
                    if any(p in buf_lower for p in SU_PROMPTS):
                        chan.sendall((bpass + '\n').encode('utf-8'))
                        pass_sent     = True
                        pass_attempts += 1
                        continue

                # ── Yanlış şifre: tekrar prompt geldi ──────────────
                if actual_method in ('sudo','su') and pass_sent and pass_attempts < MAX_ATTEMPTS:
                    check = buf_lower
                    wrong = (b'sorry' in check or b'incorrect' in check or
                             b'authentication failure' in check or
                             b'3 incorrect' in check)
                    if wrong:
                        output_lines.append('[HATA] Become şifresi yanlış!')
                        log_cb('[HATA] Become şifresi yanlış! Lütfen sunucu ayarlarını kontrol edin.')
                        chan.close(); client.close()
                        return 1, '\n'.join(output_lines)

                # ── Satır satır çıktıyı işle ────────────────────────
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    decoded = line.decode('utf-8', errors='replace').rstrip('\r')

                    # sudo/su gürültüsünü filtrele
                    if actual_method in ('sudo','su'):
                        dl = decoded.lower()
                        if (decoded.strip() == '' or
                            'sudo_pass_prompt' in dl or
                            'sudo:' in dl and 'password' in dl or
                            any(p.decode('utf-8','replace') in dl for p in SU_PROMPTS)):
                            continue

                    output_lines.append(decoded)
                    log_cb(decoded)

            elif chan.exit_status_ready():
                # Kanaldaki kalan veriyi boşalt
                while chan.recv_ready():
                    buf += chan.recv(8192)
                if buf:
                    for ln in buf.decode('utf-8', errors='replace').split('\n'):
                        ln = ln.rstrip('\r')
                        if not ln.strip():
                            continue
                        dl = ln.lower()
                        if actual_method in ('sudo','su') and (
                            'sudo_pass_prompt' in dl or
                            any(p.decode('utf-8','replace') in dl for p in SU_PROMPTS)
                        ):
                            continue
                        output_lines.append(ln)
                        log_cb(ln)
                exit_code = chan.recv_exit_status()
                break
            else:
                # Timeout check — only for sudo/su when password not yet sent
                if actual_method in ('sudo', 'su') and not pass_sent:
                    if time.monotonic() > prompt_deadline:
                        error_msg = "Sudo prompt not received — check become password or sudoers config"
                        output_lines.append(f"[HATA] {error_msg}")
                        log_cb(f"[HATA] {error_msg}")
                        chan.close()
                        client.close()
                        return 1, '\n'.join(output_lines)
                time.sleep(0.05)

        client.close()

    except (SSHConnectionError, SSHAuthenticationError):
        raise
    except Exception as e:
        msg = f"[SSH HATA] {str(e)}"
        output_lines.append(msg)
        log_cb(msg)
        exit_code = -1

    return exit_code, '\n'.join(output_lines)


# ─────────────────────────────────────────────────────────────
# BAĞLANTI TESTİ
# ─────────────────────────────────────────────────────────────
def ssh_test_connection(server):
    """
    Bağlantı testi: SSH bağlantısı + become testi.
    Döner: (ok: bool, mesaj: str)
    """
    try:
        # 1. Temel SSH bağlantısı
        client = build_ssh_client(server)
        _, stdout, stderr = client.exec_command('id && uname -r', timeout=10)
        id_out   = stdout.read().decode().strip()
        err_out  = stderr.read().decode().strip()
        client.close()

        if not id_out:
            return False, f"SSH bağlandı ancak komut çalışmadı.\nHata: {err_out}"

        method = server.get('become_method', 'none')
        if method == 'none':
            return True, f"SSH OK\n{id_out}"

        # 2. Become testi
        buser = (server.get('become_user', 'root') or 'root').strip()
        bpass = _get_become_password(server)

        # Become öncesi hangi kullanıcı olduğunu göster
        ssh_user = server.get('ssh_user', '?')

        ec, out = ssh_exec_stream(server, 'id && whoami', lambda x: None)
        actual_lines = [ln.strip() for ln in out.strip().split('\n') if ln.strip()]
        actual_user  = actual_lines[-1] if actual_lines else ''

        if ec == 0 and (actual_user == buser or f'uid=0({buser})' in out or f'({buser})' in out):
            return True, (
                f"SSH OK — {ssh_user} → become({method}) → {buser} ✓\n"
                f"SSH kullanıcı: {id_out.split(chr(10))[0]}\n"
                f"Become sonrası: {actual_lines[0] if actual_lines else '?'}"
            )
        else:
            # Hata nedenini tespit et
            hint = ""
            if 'yanlış' in out.lower() or 'incorrect' in out.lower() or 'sorry' in out.lower():
                hint = f"\nNeden: Şifre yanlış. 'Become şifresi' alanını kontrol edin."
                if server.get('become_same_pass', 1) == 1:
                    hint += f"\n(Şu an SSH şifresi kullanılıyor: become_same_pass=1)"
            elif 'not in the sudoers' in out.lower() or 'is not allowed' in out.lower():
                hint = f"\nNeden: {ssh_user} sudoers'da yok.\nÇözüm: Hedef sunucuda → sudo visudo → '{ssh_user} ALL=(ALL) NOPASSWD: ALL'"
            elif 'command not found' in out.lower():
                hint = f"\nNeden: sudo/su bulunamadı."
            elif not bpass:
                hint = f"\nNeden: Şifre boş. Sunucu ayarlarında şifre girin."

            return False, (
                f"SSH OK ancak become başarısız!\n"
                f"SSH kullanıcı: {ssh_user}, Become: {method} → {buser}\n"
                f"Beklenen: {buser} | Dönen: {actual_user!r}\n"
                f"Çıkış kodu: {ec}{hint}"
            )

    except (SSHConnectionError, SSHAuthenticationError) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# OS BİLGİSİ
# ─────────────────────────────────────────────────────────────
def ssh_get_os_info(server):
    """OS bilgisini alır. Become gerekebilir (genellikle gerekmez ama tutarlılık için)."""
    try:
        client = build_ssh_client(server)
        _, stdout, _ = client.exec_command(
            'cat /etc/os-release 2>/dev/null | head -5', timeout=10
        )
        out = stdout.read().decode().strip()
        client.close()
        return out
    except (SSHConnectionError, SSHAuthenticationError):
        raise
    except Exception:
        return ''


# ─────────────────────────────────────────────────────────────
# DOSYA YÜKLEME
# ─────────────────────────────────────────────────────────────
def ssh_upload_file(server, content, remote_path):
    """
    Dosyayı uzak sunucuya yazar.
    Become gerekiyorsa:
      1) /tmp'ye normal kullanıcı ile yaz (SFTP)
      2) become ile mv + chmod
    """
    import io
    import tempfile
    import posixpath

    method = server.get('become_method', 'none')

    try:
        client = build_ssh_client(server)
        sftp   = client.open_sftp()

        if method == 'none':
            # Doğrudan yaz
            with sftp.open(remote_path, 'w') as f:
                f.write(content)
            sftp.close()
            client.close()
            return True, 'OK'
        else:
            # Önce /tmp'ye yaz
            tmp_path = f"/tmp/.rear_upload_{secrets.token_hex(6)}"
            with sftp.open(tmp_path, 'w') as f:
                f.write(content)
            sftp.close()
            client.close()

            # Sonra become ile taşı
            mv_cmd = (
                f"mv -f {shlex.quote(tmp_path)} {shlex.quote(remote_path)} && "
                f"chmod 600 {shlex.quote(remote_path)}"
            )
            ec, out = ssh_exec_stream(server, mv_cmd, lambda x: None)
            if ec == 0:
                return True, 'OK'
            else:
                return False, f"mv başarısız (kod {ec}): {out}"

    except (SSHConnectionError, SSHAuthenticationError) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

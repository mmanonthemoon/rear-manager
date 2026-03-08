# ReaR Manager v2.2
### Merkezi Yedekleme ve Ansible Yönetim Paneli

> **Tamamen Offline Çalışır** — İnternet gerektirmez. Tek sunucu, tek süreç, sıfır dış bağımlılık.

---

## İçindekiler

1. [Genel Bakış](#genel-bakış)
2. [Teknoloji Altyapısı](#teknoloji-altyapısı)
3. [Mimari ve Dosya Yapısı](#mimari-ve-dosya-yapısı)
4. [Özellikler](#özellikler)
5. [Gereksinimler](#gereksinimler)
6. [Kurulum](#kurulum)
7. [İlk Başlatma ve Yapılandırma](#ilk-başlatma-ve-yapılandırma)
8. [ReaR Yedekleme Modülü](#rear-yedekleme-modülü)
9. [Ansible Modülü](#ansible-modülü)
10. [Offline Paket Yönetimi](#offline-paket-yönetimi)
11. [Windows Yönetimi](#windows-yönetimi)
12. [Kullanıcı Yönetimi ve Active Directory](#kullanıcı-yönetimi-ve-active-directory)
13. [Sorun Giderme](#sorun-giderme)

---

## Genel Bakış

ReaR Manager v2.0, ağ üzerindeki Linux ve Windows sunucularına **merkezi** olarak:
- **ReaR (Relax-and-Recover)** tabanlı bare-metal yedekleme yönetimi
- **Ansible** otomasyon ve yapılandırma yönetimi

sağlayan, tamamen **offline ortamlar** için tasarlanmış bir web uygulamasıdır.

### Neden Bu Araç?

| İhtiyaç | Çözüm |
|---------|-------|
| İnternet yok | Tüm paketler önceden indirilir, offline kurulum yapılır |
| Birden fazla sunucu | Merkezi panelden tüm sunucular yönetilir |
| Root olmayan kullanıcılar | `sudo`/`su` become desteği |
| Windows + Linux karışık ortam | SSH + WinRM desteği, tek panel |
| Yedeklem izleme | Gerçek zamanlı log, zamanlayıcı, geçmiş |
| Otomasyon | Ansible playbook + rol yönetimi GUI'den |

---

## Teknoloji Altyapısı

### Backend

| Teknoloji | Versiyon | Kullanım Amacı |
|-----------|---------|----------------|
| **Python** | 3.8+ | Ana uygulama dili |
| **Flask** | 2.3+ | Web framework, HTTP routing, Jinja2 template engine |
| **Werkzeug** | 2.3+ | WSGI server, güvenli hash (şifre), HTTP utilities |
| **SQLite** | 3.x (built-in) | Veritabanı — kurulum gerektirmez, tek dosya |
| **Paramiko** | 3.0+ | SSH protokolü: bağlantı, komut çalıştırma, SFTP dosya transfer |
| **APScheduler** | 3.10+ | Zamanlanmış görevler (cron-tarzı yedekleme planlaması) |
| **ldap3** | 2.9+ *(opsiyonel)* | Active Directory / LDAP kimlik doğrulama |
| **pywinrm** | 0.4+ *(opsiyonel)* | Windows WinRM üzerinden Ansible host ping testi |
| **PyYAML** | *(Ansible ile gelir)* | Ansible inventory YAML üretimi |

### Frontend

| Teknoloji | Versiyon | Kullanım Amacı |
|-----------|---------|----------------|
| **HTML5 + CSS3** | — | Tüm arayüz |
| **Vanilla JavaScript** | ES6+ | AJAX polling, modal, dinamik form alanları |
| **Jinja2** | 3.x | Server-side template engine (Flask içinde gelir) |
| **Font-based ikonlar** | Unicode emoji | Harici CSS/JS kütüphanesi yok |

> **Not:** Node.js, npm, webpack, React, Angular yoktur. Tüm frontend saf HTML/CSS/JS'tir.

### Yedekleme Altyapısı

| Teknoloji | Kullanım Amacı |
|-----------|----------------|
| **ReaR (Relax-and-Recover)** | Hedef sunuculara kurulan bare-metal kurtarma aracı |
| **NFS** | Yedek dosyalarının merkezi sunucuya yazılması |
| **ISO/NETFS** | ReaR çıktı formatı (kurtarma ISO'su + arşiv) |
| **dpkg / apt** | Ubuntu offline paket kurulumu |
| **tar.gz** | Offline paket transfer arşivi |

### Otomasyon Altyapısı

| Teknoloji | Kullanım Amacı |
|-----------|----------------|
| **Ansible** | Playbook çalıştırma, host yönetimi |
| **ansible-playbook** | CLI aracı (arka planda çalışır) |
| **WinRM** | Windows uzak yönetim protokolü |
| **SSH** | Linux uzak bağlantı protokolü |
| **NTLM / Kerberos / Basic** | Windows kimlik doğrulama transportları |

### Veritabanı Şeması

```
SQLite (rear_manager.db)
├── users               — Kullanıcı hesapları, roller
├── settings            — Uygulama ayarları (anahtar-değer)
├── servers             — ReaR yedek sunucuları
├── backup_jobs         — Yedekleme işleri ve logları
├── schedules           — Otomatik yedekleme zamanlamaları
├── ansible_hosts       — Ansible yönetilen hostlar
├── ansible_groups      — Host grupları (hiyerarşik)
├── ansible_host_groups — Host-Grup ilişki tablosu
├── ansible_playbooks   — Playbook YAML içerikleri
├── ansible_runs        — Playbook çalıştırma geçmişi + loglar
├── ansible_roles       — Ansible rolleri
└── ansible_role_files  — Rol dosyaları (tasks/handlers/vars/...)
```

---

## Mimari ve Dosya Yapısı

```
/opt/rear-manager/
│
├── app.py                      ← Ana uygulama (~4500+ satır, tüm route + iş mantığı)
├── rear_manager.db             ← SQLite veritabanı (otomatik oluşur)
├── install.sh                  ← Otomatik kurulum betiği
├── prepare_offline_packages.sh ← Ubuntu offline paket hazırlama
├── requirements.txt            ← Python bağımlılıkları
│
├── templates/                  ← Jinja2 HTML şablonları (25 dosya)
│   ├── base.html               ← Ana layout (sidebar, topbar, modal, CSS)
│   ├── dashboard.html
│   ├── servers.html / server_detail.html / server_form.html
│   ├── configure.html          ← ReaR yapılandırma formu
│   ├── jobs.html / job_detail.html
│   ├── server_bulk.html        ← Toplu sunucu import
│   ├── settings.html           ← Genel/AD/Offline/Araçlar sekmeleri
│   ├── users.html / user_form.html
│   ├── ansible_dashboard.html
│   ├── ansible_hosts.html / ansible_host_form.html
│   ├── ansible_groups.html
│   ├── ansible_playbooks.html / ansible_playbook_editor.html
│   ├── ansible_run_form.html / ansible_run_detail.html / ansible_runs.html
│   └── ansible_roles.html / ansible_role_editor.html
│
├── static/                     ← Statik dosyalar (CSS, JS — opsiyonel)
│
├── ansible/                    ← Ansible workspace (otomatik oluşur)
│   ├── ansible.cfg             ← Ansible yapılandırması
│   ├── inventories/
│   │   └── hosts.yml           ← Otomatik üretilir (DB'den)
│   ├── playbooks/              ← .yml dosyaları (DB ile senkron)
│   ├── roles/                  ← Rol dizin yapısı (DB ile senkron)
│   ├── group_vars/             ← Grup değişkenleri (.yml)
│   └── host_vars/              ← Host değişkenleri (.yml)
│
└── offline-packages/           ← Ubuntu offline .deb paketleri
    ├── focal/                  ← Ubuntu 20.04
    ├── jammy/                  ← Ubuntu 22.04
    ├── noble/                  ← Ubuntu 24.04
    └── plucky/                 ← Ubuntu 25.04
```

---

## Özellikler

### ReaR Modülü
- Sunucu ekleme/düzenleme/silme (SSH şifre veya anahtar ile)
- `sudo`/`su` become desteği (root olmayan kullanıcılarla bağlantı)
- Toplu sunucu import (CSV veya metin yapıştırma)
- **Ubuntu offline paket kurulumu** — internet olmadan .deb ile kurulum
- ReaR yapılandırma (NFS URL, OUTPUT, BACKUP, AUTORESIZE, hariç dizinler)
- **Otomatik yapılandırma** — sunucu eklendiğinde global ayarlardan varsayılan ReaR config otomatik uygulanır
- **Otomatik NFS dizin oluşturma** — `rear mkbackup` öncesi hedef dizin yoksa otomatik oluşturulur
- Yedekleme başlatma ve canlı log izleme
- Zamanlayıcı (cron tarzı — dakika/saat/gün/ay/haftanın günü)
- Yedekleme geçmişi ve dosya listesi (gerçek boyut hesaplama `du -sb` ile)

### Ansible Modülü
- **Linux host**: SSH + become (sudo/su, aynı/farklı şifre)
- **Windows host**: WinRM (NTLM/Kerberos/Basic), domain veya workgroup
- Host grupları (hiyerarşik yapı destekli)
- Host başına `host_vars`, grup başına `group_vars`
- Inventory otomatik üretimi (DB → `hosts.yml`)
- Playbook editörü (YAML, tab desteği)
- Playbook çalıştırma (limit, tag, extra-vars, verbosity, check mode)
- Canlı run log (2sn polling, iptal butonu)
- Rol editörü (tasks/handlers/vars/defaults/meta/templates/files bölümleri)
- Rol dosyası ekleme ve içerik düzenleme
- Çalışma geçmişi ve log arşivi

### Genel
- Oturum tabanlı kimlik doğrulama
- Admin / User rol ayrımı
- Active Directory / LDAP entegrasyonu (opsiyonel)
- Offline paket durumu izleme (Ayarlar paneli)
- SSH anahtar çifti üretimi

---

## Gereksinimler

### Merkezi Sunucu (ReaR Manager kurulacak)

| Gereksinim | Minimum | Önerilen |
|-----------|---------|---------|
| İşletim Sistemi | Ubuntu 20.04+ / RHEL 8+ / Debian 11+ | Ubuntu 22.04 LTS |
| CPU | 1 core | 2+ core |
| RAM | 512 MB | 2 GB |
| Disk | 10 GB | 50+ GB (yedek dosyaları için) |
| Python | 3.8+ | 3.10+ |
| Ağ Erişimi | SSH (22) hedeflere | SSH + WinRM (5985/5986) |

### Hedef Sunucular

**Linux:**
- SSH servisi açık (port 22 veya özel)
- Kullanıcının sudo/su yetkisi var (root değilse)
- ReaR kurulabilecek boş disk alanı (~50 MB)

**Windows:**
- WinRM servisi aktif (bkz. [Windows WinRM Kurulumu](#windows-winrm-kurulumu))
- Port 5985 (HTTP) veya 5986 (HTTPS) açık
- Yönetici yetkili kullanıcı

---

## Kurulum

### Adım 1: Dosyaları Sunucuya Kopyalayın

```bash
# zip dosyasını kopyalayın
scp rear-manager-v2.zip root@<merkezi_sunucu_ip>:/tmp/

# Sunucuya bağlanın
ssh root@<merkezi_sunucu_ip>

# Açın
cd /tmp
unzip rear-manager-v2.zip
cd rear-manager
```

### Adım 2: Otomatik Kurulum

```bash
# Kurulum betiğini çalıştırın (root gerektirir)
sudo bash install.sh
```

Betik şunları yapar:
1. Sistem paketlerini kurar (python3, python3-pip, python3-venv, libldap, sshpass)
2. `/opt/rear-manager/` dizinini oluşturur
3. Python sanal ortamını (venv) oluşturur
4. Flask, Paramiko, APScheduler, ldap3, pywinrm paketlerini kurar
5. Ansible'ı sistem paketi veya pip ile kurar
6. SSH anahtar çifti oluşturur
7. systemd servisini oluşturur ve başlatır
8. Firewall portunu açar (80/tcp)

### Adım 3: Kurulumu Doğrulayın

```bash
# Servis durumunu kontrol edin
systemctl status rear-manager

# Logları izleyin
journalctl -u rear-manager -f
```

Beklenen çıktı:
```
ReaR Manager v2.2 - Merkezi Yedekleme Yönetim Paneli
Adres     : http://0.0.0.0:80
DB        : /opt/rear-manager/rear_manager.db
Yedekler  : /srv/rear-backups
Scheduler : APScheduler ✓
LDAP/AD   : ldap3 ✓
Varsayılan: admin / admin123
```

### Adım 4: Tarayıcıdan Erişin

```
http://<merkezi_sunucu_ip>
```

Varsayılan giriş:
- **Kullanıcı:** `admin`
- **Şifre:** `admin123` ← **İlk girişte mutlaka değiştirin!**

---

### Manuel Kurulum (Otomatik Betik Çalışmazsa)

```bash
# 1. Gerekli sistem paketleri (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y \
    python3 python3-pip python3-venv python3-dev \
    gcc libldap2-dev libsasl2-dev libssl-dev \
    openssh-client sshpass ansible

# 2. Dizinler
sudo mkdir -p /opt/rear-manager/{templates,static,ansible,offline-packages}
sudo mkdir -p /srv/rear-backups
sudo chmod 777 /srv/rear-backups

# 3. Dosyaları kopyala
sudo cp app.py /opt/rear-manager/
sudo cp -r templates/ /opt/rear-manager/
sudo cp prepare_offline_packages.sh /opt/rear-manager/

# 4. Python sanal ortamı
cd /opt/rear-manager
python3 -m venv venv
./venv/bin/pip install flask paramiko apscheduler werkzeug ldap3 pywinrm

# 5. Ansible (sistem paketi yoksa)
./venv/bin/pip install ansible

# 6. SSH anahtarı (opsiyonel)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/rear_manager_rsa -N "" -C "rear-manager"

# 7. Servisi oluştur
sudo tee /etc/systemd/system/rear-manager.service > /dev/null <<EOF
[Unit]
Description=ReaR Manager v2.2
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/rear-manager
ExecStart=/opt/rear-manager/venv/bin/python3 /opt/rear-manager/app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable rear-manager
sudo systemctl start rear-manager
```

---

## İlk Başlatma ve Yapılandırma

### 1. Şifre Değiştirme

`http://<ip>:5000` → Sol menü → **Şifre Değiştir**

### 2. Yedek Dizini Yapılandırması

Sol menü → **Ayarlar** → Genel sekmesi:

| Alan | Değer |
|------|-------|
| Yedek Sunucu IP / Hostname | Bu sunucunun IP'si (ör: 192.168.1.1) |
| Yedek Dizini (Export Yolu) | `/srv/rear-backups` |

> **Not:** NFS/SMB kurulumu arayüzden yapılmaz. Linux sunucusunda `nfs-kernel-server` servisi ile `/srv/rear-backups` dizinini export etmeniz gerekir. ReaR Manager yalnızca BACKUP_URL'yi (`nfs://<ip><yol>/<hostname>`) üretir.

### 3. İlk Sunucu Ekleme

Sol menü → **Sunucular** → **Sunucu Ekle**

**Root ile bağlanan sunucu:**
```
Etiket:        Web Sunucusu 1
Hostname/IP:   192.168.1.101
SSH Kullanıcı: root
Auth:          Şifre veya SSH Anahtarı
Become:        Yok
```

**Ubuntu (ubuntu kullanıcısı + sudo):**
```
Etiket:        App Server
Hostname/IP:   192.168.1.102
SSH Kullanıcı: ubuntu
Auth:          Şifre
Become:        sudo → root
Become Şifresi: SSH şifresi ile aynı ✓
```

→ **Kaydet** → **Bağlantı Testi** → **ReaR Kur** → (ReaR kurulunca) **Yapılandır** → (Yapılandırma sonrası) **Yedekle**

---

## NFS Yapılandırması (Linux Sunucu)

ReaR Manager, NFS kurulumunu kendiniz yapmanızı bekler. Merkezi sunucuda şu komutları çalıştırın:

```bash
# Ubuntu/Debian
apt-get install -y nfs-kernel-server
mkdir -p /srv/rear-backups
chmod 777 /srv/rear-backups

# /etc/exports dosyasına ekle:
echo "/srv/rear-backups  *(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
exportfs -ra
systemctl enable --now nfs-kernel-server
```

```bash
# RHEL/Rocky/AlmaLinux
dnf install -y nfs-utils
mkdir -p /srv/rear-backups
chmod 777 /srv/rear-backups
echo "/srv/rear-backups  *(rw,sync,no_subtree_check,no_root_squash)" >> /etc/exports
exportfs -ra
systemctl enable --now nfs-server
```

ReaR Manager'da **Ayarlar → Genel** sekmesinden Yedek Sunucu IP ve Yedek Dizini girin. Uygulama BACKUP_URL'yi otomatik üretir: `nfs://<ip><yol>/<hostname>`

---

## ReaR Yedekleme Modülü

### Yedekleme Akışı

```
GUI → SSH bağlantısı → Hedef sunucu
           ↓
    [Offline ise]
    .deb paketleri SFTP → dpkg -i → ReaR kurulur
           ↓
    /etc/rear/local.conf yazılır
           ↓
    rear mkbackup çalışır
           ↓
    NFS üzerinden /srv/rear-backups/<hostname>/ yazılır
           ↓
    ISO + tar.gz arşivi oluşur
```

### Yapılandırma Seçenekleri

| Seçenek | Açıklama | Varsayılan |
|---------|---------|-----------|
| OUTPUT | Çıktı tipi | ISO |
| BACKUP | Yedek tipi | NETFS |
| MIGRATION_MODE | Farklı donanımda kurtarma | Aktif |
| AUTORESIZE | Disk boyutu otomatik ayarla | Aktif |
| Hariç Dizinler | /tmp, /var/tmp vb. | Önceden tanımlı |

### Zamanlama

Sunucu Detay → **Zamanlama Ekle**

```
Dakika: 0
Saat:   2
Gün:    *  (Her gün)
Ay:     *
Haftanın Günü: *
```

Bu örnek her gece 02:00'de yedek alır.

---

## Ansible Modülü

### Linux Host Ekleme

Sol menü → **Ansible** → **Hostlar** → **Host Ekle**

```
Ad:              app-server-01
Hostname/IP:     192.168.1.50
OS Tipi:         Linux
Bağlantı:        SSH (port 22)
Kullanıcı:       ubuntu
Auth:            Şifre
Become:          sudo → root
Become Şifresi:  SSH ile aynı ✓
```

### Windows Host Ekleme

```
Ad:              win-server-01
Hostname/IP:     192.168.1.100
OS Tipi:         Windows
Bağlantı:        WinRM (port 5985)
Scheme:          http
Kullanıcı:       Administrator  veya  CORP\Administrator
Şifre:           Windows şifresi
Transport:       NTLM (domain) / Basic (workgroup)
Domain:          CORP.LOCAL (domain varsa)
```

### Grup Oluşturma ve Host Atama

1. **Ansible** → **Gruplar** → **Grup Adı** girin → **Ekle**
2. **Ansible** → **Hostlar** → Host düzenle → **Grup Seç**
3. Grup değişkenleri (YAML) girilebilir

### Playbook Yazma ve Çalıştırma

**Ansible** → **Playbooklar** → **Yeni Playbook**

```yaml
---
- name: Web Sunucularını Güncelle
  hosts: webservers
  become: yes
  tasks:
    - name: Sistem güncellemesi
      apt:
        update_cache: yes
        upgrade: dist
      when: ansible_os_family == "Debian"

- name: Windows Servisleri Kontrol
  hosts: windows_servers
  tasks:
    - name: Servis durumu
      win_service:
        name: W32Time
        state: started
```

→ **Kaydet** → **▶ Çalıştır**

Çalışma seçenekleri:
- **Limit:** Belirli host veya grup (ör: `web01`, `webservers`)
- **Etiket:** Belirli task'ları çalıştır
- **Extra Vars:** `key=value` veya JSON
- **Verbosity:** -v, -vv, -vvv (debug seviyesi)
- **Check Mode:** Gerçekte yapmadan simüle et (--check)

### Rol Oluşturma

**Ansible** → **Roller** → **Rol Adı** girin → **Oluştur ve Düzenle**

Otomatik oluşturulan bölümler:
- `tasks/main.yml` — Ana görevler
- `handlers/main.yml` — Olaya bağlı handler'lar
- `vars/main.yml` — Sabit değişkenler
- `defaults/main.yml` — Geçersiz kılınabilir varsayılanlar
- `meta/main.yml` — Rol bağımlılıkları

---

## Offline Paket Yönetimi

Ubuntu hedef sunucuların internete erişimi yoksa ReaR'ı yükleyemezsiniz. Bu durumda:

### Adım 1: Paketleri İndir (İnternet Olan Makinede)

```bash
# Ubuntu 22.04 (jammy) için — o sürüm VM'inde çalıştırın:
sudo bash /opt/rear-manager/prepare_offline_packages.sh

# Tüm Ubuntu sürümleri için Docker ile (Docker varsa):
sudo bash /opt/rear-manager/prepare_offline_packages.sh --all-docker
```

Paketler şuraya yazılır: `/opt/rear-manager/offline-packages/<codename>/`

### Adım 2: Merkezi Sunucuya Kopyala

```bash
# İnternet olan makineden merkezi sunucuya kopyala:
rsync -avz /opt/rear-manager/offline-packages/ \
    root@192.168.1.1:/opt/rear-manager/offline-packages/

# veya scp ile:
scp -r /opt/rear-manager/offline-packages/ \
    root@192.168.1.1:/opt/rear-manager/
```

### Adım 3: Durumu Kontrol Et

Tarayıcı → **Ayarlar** → **Offline Paketler** sekmesi

| Ubuntu | Codename | Durum |
|--------|---------|-------|
| 20.04 | focal | ✓ Hazır (48 paket, 95 MB) |
| 22.04 | jammy | ✓ Hazır (52 paket, 102 MB) |
| 24.04 | noble | ✗ Eksik |
| 25.04 | plucky | ✗ Eksik |

### Kurulum Sırası (Otomatik)

```
ReaR Kur butonu tıklandığında:
1. Hedef sunucunun Ubuntu codename'i tespit edilir
2. offline-packages/<codename>/*.deb → tar.gz arşivlenir
3. SFTP ile hedef sunucuya kopyalanır
4. dpkg -i ile kurulur (2 geçiş — bağımlılık sırası için)
5. apt-get install -f (yerel bağımlılık düzeltme)
6. Geçici dosyalar silinir
```

---

## Windows Yönetimi

### WinRM Kurulumu (Hedef Windows Sunucularda)

Windows sunucusunda PowerShell ile (Administrator olarak):

```powershell
# WinRM'yi etkinleştir
Enable-PSRemoting -Force

# HTTP üzerinden (5985) — test ortamı
winrm set winrm/config/service '@{AllowUnencrypted="true"}'
winrm set winrm/config/service/auth '@{Basic="true"}'

# NTLM kimlik doğrulamasını etkinleştir
winrm set winrm/config/service/auth '@{Negotiate="true"}'

# Firewall kuralı ekle
netsh advfirewall firewall add rule name="WinRM-HTTP" dir=in action=allow protocol=TCP localport=5985

# Servis başlat
net start WinRM
sc config WinRM start=auto
```

### Domain Ortamı için (NTLM)

```powershell
# Domain ortamında ek yapılandırma gerekmez
# Kullanıcı adını şu formatta girin:
# CORP\Administrator  veya  Administrator@CORP.LOCAL
```

### Bağlantı Testi

Host eklendikten sonra → **🔗 Ping** butonu

Başarılı çıktı:
```
✓ BAŞARILI

win-server-01 | SUCCESS => {
    "changed": false,
    "ping": "pong"
}
```

---

## Kullanıcı Yönetimi ve Active Directory

### Yerel Kullanıcı Oluşturma

Sol menü → **Kullanıcılar** → **Kullanıcı Ekle**

| Alan | Açıklama |
|------|---------|
| Kullanıcı Adı | Benzersiz, küçük harf |
| Şifre | Yerel hesaplar için zorunlu |
| Rol | `admin` — tam yetki, `user` — sınırlı erişim |
| Auth Tipi | `Yerel` veya `Active Directory` |

### Active Directory Entegrasyonu

Sol menü → **Ayarlar** → **Active Directory** sekmesi:

```
AD Sunucusu:   dc01.corp.local  (veya IP)
Port:          389 (LDAP) veya 636 (LDAPS)
Domain:        CORP.LOCAL
Base DN:       DC=corp,DC=local
Bind User:     svc-rear@corp.local
Bind Şifresi:  ********
User Filter:   (sAMAccountName={username})
Admin Grubu:   CN=IT-Admins,OU=Groups,DC=corp,DC=local
User Grubu:    CN=IT-Users,OU=Groups,DC=corp,DC=local
```

AD kullanıcıları **şifresiz** eklenebilir (Auth Tipi: Active Directory seçilir).

---

## Sorun Giderme

### Servis Başlamıyor

```bash
# Log kontrol
journalctl -u rear-manager -n 50 --no-pager

# Manuel çalıştırma (hata detayı için)
cd /opt/rear-manager
./venv/bin/python3 app.py
```

### SSH Bağlantısı Başarısız

```bash
# Merkezi sunucudan hedef sunucuya test:
ssh -p 22 ubuntu@192.168.1.101

# SSH anahtarı kullanıyorsanız:
ssh -i ~/.ssh/rear_manager_rsa ubuntu@192.168.1.101

# Become testi:
ssh ubuntu@192.168.1.101 "echo 'pass' | sudo -S whoami"
```

### ReaR Kurulumu Başarısız — Offline Paket Yok

```
ÇÖZÜM: Internet olan Ubuntu VM'de:
sudo bash /opt/rear-manager/prepare_offline_packages.sh

Sonra merkezi sunucuya kopyalayın:
rsync -avz /opt/rear-manager/offline-packages/ \
    root:/opt/rear-manager/offline-packages/
```

### Ansible Playbook Çalışmıyor

```bash
# Ansible kurulu mu?
ansible --version

# Kurulu değilse:
/opt/rear-manager/venv/bin/pip install ansible

# WinRM Python kütüphanesi
/opt/rear-manager/venv/bin/pip install pywinrm

# Manuel inventory testi:
ansible -i /opt/rear-manager/ansible/inventories/hosts.yml all -m ping
```

### Windows WinRM Bağlantısı Başarısız

```powershell
# Windows'ta WinRM durumu:
winrm enumerate winrm/config/listener

# Bağlantı testi (Windows'tan):
Test-WSMan -ComputerName localhost

# Servis yeniden başlat:
Restart-Service WinRM
```

### NFS Mount Edilemiyor / Yedek Başarısız

```bash
# NFS export kontrol (merkezi sunucuda):
exportfs -v

# NFS servisi çalışıyor mu?
systemctl status nfs-kernel-server   # Ubuntu/Debian
systemctl status nfs-server          # RHEL/Rocky

# Hedef sunucudan mount testi:
mount -t nfs 192.168.1.1:/srv/rear-backups /mnt/test

# Export listesini gör:
showmount -e 192.168.1.1
```

### Veritabanı Sıfırlama (DİKKAT: Tüm veri silinir)

```bash
systemctl stop rear-manager
rm /opt/rear-manager/rear_manager.db
systemctl start rear-manager
# Yeni DB otomatik oluşturulur, admin/admin123 ile giriş
```

---

## Servis Yönetimi

```bash
# Durumu görüntüle
systemctl status rear-manager

# Yeniden başlat
systemctl restart rear-manager

# Durdur
systemctl stop rear-manager

# Canlı log izle
journalctl -u rear-manager -f

# Son 100 satır log
journalctl -u rear-manager -n 100 --no-pager
```

---

## Güvenlik Notları

> Bu uygulama tamamen offline, güvenli iç ağ ortamı için tasarlanmıştır.

- HTTPS yoktur (gerekirse nginx reverse proxy + Let's Encrypt eklenebilir)
- Şifreler SQLite'ta saklanır (bcrypt hash — kullanıcı şifreleri, SSH/become şifreleri düz metin)
- Varsayılan şifreyi (`admin123`) ilk girişte **mutlaka** değiştirin
- Sunucuya ağ erişimini firewall ile kısıtlayın (sadece yöneticiler erişebilsin)
- SSH anahtarı kullanımı şifreye tercih edilir

---

## Versiyon Bilgisi

| Modül | Durum |
|-------|-------|
| ReaR Yedekleme | ✅ Tam işlevsel |
| Offline Ubuntu Kurulum | ✅ Tam işlevsel |
| Toplu Sunucu Import | ✅ Tam işlevsel |
| Ansible Linux | ✅ Tam işlevsel |
| Ansible Windows | ✅ Tam işlevsel |
| Ansible Rol Yönetimi | ✅ Tam işlevsel |
| Active Directory | ✅ Tam işlevsel |
| Zamanlayıcı | ✅ Tam işlevsel |


---

## Değişiklik Günlüğü

### v2.2 (Son Güncellemeler)

#### Yeni Özellikler
- **NFS kurulumu kaldırıldı:** NFS/SMB yapılandırması artık arayüzden yapılmıyor; Linux sunucusunda kendiniz yapılandırırsınız. Ayarlar → Genel'den yalnızca Yedek Sunucu IP ve Yedek Dizini girilir.
- **Yedek klasörü güvenli adlandırma:** Hostname'deki nokta ve özel karakterler (`web01.example.com` → `web01-example-com`) güvenli dizin adına dönüştürülür.
- **Ansible otomatik ekleme düzeltmesi:** IP adresi hostname olarak girildiğinde (`192.168.1.49`) Ansible host adı artık tam IP'den türetilir (`192-168-1-49`), `192` olarak kesilmez.

#### Hata Düzeltmeleri
- **ReaR kurulum kontrolü:** Yedekleme ve yapılandırma butonları artık ReaR kurulu/yapılandırılmış olmadan çalışmaz; hem UI'da devre dışı bırakılır hem backend'de engellenir.
- **Zamanlanmış yedekleme güvenliği:** ReaR kurulu/yapılandırılmamış sunucularda zamanlayıcı sessizce atlar, hata oluşturmaz.
- **Offline kurulum takılma sorunu:** `dpkg -i` komutları artık `DEBIAN_FRONTEND=noninteractive` ile çalışır; debconf interaktif promptları kurulumu askıya almaz.

### v2.1

#### Yeni Özellikler
- **Otomatik ReaR Yapılandırma:** Sunucu eklendiğinde global ayarlardan varsayılan `/etc/rear/local.conf` otomatik uygulanır.
- **Otomatik NFS Hedef Dizini:** `rear mkbackup` öncesi hedef dizin yoksa otomatik oluşturulur.
- **NFS Köprü (Bridge) Modu:** Harici NFS'e doğrudan erişemeyen sunucular için köprü mimarisi desteği eklendi.

#### Hata Düzeltmeleri
- Log izleme spinner'ı iş tamamlanınca "✓ Tamamlandı" olarak güncellenir.
- Yedek boyutu `du -sb` ile doğru hesaplanır (önceden 0.0 MB görünüyordu).
- SSH bağlantısı, become yetkilendirmesi ve UI hataları giderildi.

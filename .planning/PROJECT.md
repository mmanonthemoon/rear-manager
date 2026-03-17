# ReaR Manager

## What This Is

ReaR Manager, fiziksel Linux sunuculara SSH üzerinden merkezi olarak ReaR (bare-metal yedekleme) kurulumu, yapılandırması ve izlemesi ile Ansible otomasyon yönetimi sağlayan bir web uygulamasıdır. Tamamen offline (internet bağlantısı gerektirmez) çalışacak şekilde tasarlanmıştır. Uygulama, Python/Flask + SQLite + Paramiko üzerine kurulu; vanilla HTML/CSS/JS ile saf server-side rendering kullanır.

## Core Value

Hava boşluklu (air-gapped) ağlardaki IT yöneticilerinin, fiziksel Linux sunucularda ReaR yedeklerini ve Ansible otomasyonunu tek bir panelden yönetebilmesi — internet olmadan, harici servis olmadan.

## Requirements

### Validated

<!-- Mevcut v2.2'de çalışan özellikler -->

- ✓ SSH ile uzak sunuculara ReaR kurulumu (online + offline .deb paketi) — v2.2
- ✓ ReaR yapılandırma ve `rear mkbackup` çalıştırma, canlı log izleme — v2.2
- ✓ Cron-tarzı yedekleme zamanlayıcısı (APScheduler) — v2.2
- ✓ Ansible host/grup/playbook/rol yönetimi (Linux SSH + Windows WinRM) — v2.2
- ✓ Otomatik inventory üretimi (DB → hosts.yml) — v2.2
- ✓ Toplu sunucu import (CSV / metin yapıştırma) — v2.2
- ✓ Oturum tabanlı kimlik doğrulama, admin/user rol ayrımı — v2.2
- ✓ Active Directory / LDAP entegrasyonu — v2.2
- ✓ Offline Ubuntu paket durumu izleme — v2.2

### Active

<!-- Mevcut milestone için yapılacaklar -->

- [ ] Race condition düzeltmesi: `_running_jobs` dict tüm kod yollarında `_job_lock` ile korunmalı
- [ ] SSH PTY / sudo prompt tespiti güvenilirleştirilmeli (farklı OS varyantlarında çalışacak)
- [ ] APScheduler timezone drift düzeltmesi: `init_scheduler()` içinde açık timezone tanımlanmalı
- [ ] `app.py` modüllerine ayrılmalı (routes / services / models katmanları)
- [ ] Kritik fonksiyonlar için test coverage eklenmeli
- [ ] Sayfalama: jobs, servers, ansible runs listelerinde LIMIT/OFFSET ile sayfa desteği
- [ ] Denetim logu: kim hangi yedekleme / Ansible komutunu çalıştırdı kaydı
- [ ] Log boyutu limiti: `backup_jobs.output` ve `ansible_runs` DB sütunlarına maksimum boyut kontrolü

### Out of Scope

- Güvenlik sertleştirmesi (CSRF, SSH host key doğrulama, şifre şifreleme) — sonraki milestone
- E-posta / webhook uyarı sistemi — sonraki milestone
- Çok-worker WSGI dağıtımı — ölçekleme ihtiyacı yok (single-admin tool)
- HTTPS / reverse proxy kurulumu — altyapı sorumluluğu kullanıcıda

## Context

- Uygulama `/opt/rear-manager/` altında systemd servisi olarak çalışır
- Tüm iş mantığı `app.py` tek dosyasında (~4200+ satır) — Flask routes, SSH, DB, scheduler birbirine karışık
- SQLite single-file DB (`rear_manager.db`); concurrent write için yeterli (single-admin kullanım)
- Frontend tamamen server-side Jinja2; Node.js/npm/webpack yok
- Codebase map: `.planning/codebase/` dizininde detaylı analiz mevcut

## Constraints

- **Tech Stack**: Python/Flask/SQLite — değiştirilmeyecek; mevcut deployment'ları bozmaz
- **Offline**: Hiçbir harici CDN, API veya internet bağlantısı kullanılamaz
- **Geriye uyumluluk**: Refactor sonrası mevcut DB şeması ve tüm URL'ler çalışmaya devam etmeli
- **Tek süreç**: APScheduler + Flask aynı process; multi-worker WSGI desteklenmiyor

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Önce modularize, sonra test | Test yazmak için izole edilebilir katmanlara ihtiyaç var | — Pending |
| SQLite kalıcı | Kullanım senaryosu single-admin; PostgreSQL gereksiz karmaşıklık | ✓ Good |
| Vanilla HTML/CSS/JS | Offline ortamda CDN bağımlılığı olmadan çalışır | ✓ Good |
| NFS kurulumu arayüzden kaldırıldı (v2.2) | Kullanıcı kendi NFS'ini kurar; uygulama sadece URL üretir | ✓ Good |

---
*Last updated: 2026-03-17 after initialization*

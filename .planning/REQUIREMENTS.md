# Requirements: ReaR Manager

**Defined:** 2026-03-17
**Core Value:** Hava boşluklu ağlardaki IT yöneticilerinin fiziksel Linux sunucularda ReaR yedeklerini ve Ansible otomasyonunu tek bir panelden yönetebilmesi

---

## v1 Requirements

### Bug Fixes

- [ ] **BUG-01**: `_running_jobs` dict tüm erişim noktalarında `_job_lock` ile korunmalı (race condition)
- [ ] **BUG-02**: SSH PTY / sudo prompt tespiti farklı OS varyantlarında (RHEL, Ubuntu, Debian) güvenilir çalışmalı
- [ ] **BUG-03**: APScheduler başlatılırken açık timezone tanımlanmalı (timezone drift önlenmeli)
- [ ] **BUG-04**: `app.secret_key` uygulama yeniden başlatılınca oturumların kapanmaması için kalıcı hale getirilmeli

### Refactoring

- [ ] **REF-01**: `app.py` routes / services / models katmanlarına ayrılmalı (mevcut davranış değişmeden)
- [ ] **REF-02**: Tüm DB sorguları inline string yerine model/repository katmanına taşınmalı
- [ ] **REF-03**: `~48` adet bare `except Exception` bloğu yapılandırılmış hata işlemeyle değiştirilmeli

### Testing

- [ ] **TEST-01**: SSH bağlantısı ve komut çalıştırma servisleri için birim testleri yazılmalı
- [ ] **TEST-02**: ReaR kurulum ve yapılandırma akışları için entegrasyon testleri yazılmalı
- [ ] **TEST-03**: Ansible host/playbook çalıştırma akışları için test coverage eklenmeli

### Missing Features

- [ ] **FEAT-01**: Jobs, servers ve ansible runs listelerinde sayfalama (LIMIT/OFFSET, 25 kayıt/sayfa)
- [ ] **FEAT-02**: Denetim logu: kim hangi yedekleme / Ansible komutunu ne zaman çalıştırdı kaydedilmeli
- [ ] **FEAT-03**: `backup_jobs.output` ve `ansible_runs.output` sütunlarına maksimum boyut kontrolü (ör: 1 MB limit, önce truncate sonra kaydet)

---

## v2 Requirements

### Security

- **SEC-01**: SSH / become şifreleri DB'de şifreli saklanmalı (Fernet veya benzeri)
- **SEC-02**: CSRF koruması Flask-WTF ile eklenmeli
- **SEC-03**: Login endpoint'ine brute-force koruması (rate limiting) eklenmeli
- **SEC-04**: SSH host key doğrulaması `AutoAddPolicy` yerine yönetilebilir policy ile yapılmalı

### Alerting

- **ALRT-01**: Yedekleme başarısızlığında e-posta bildirimi
- **ALRT-02**: Yedekleme başarısızlığında webhook (HTTP POST) bildirimi

### Performance

- **PERF-01**: Sık kullanılan sorgular için DB index'leri eklenmeli (`server_id`, `schedule_id`, `created_at`)

---

## Out of Scope

| Feature | Reason |
|---------|--------|
| HTTPS / TLS | Kullanıcının altyapı sorumluluğu; nginx reverse proxy ile çözülür |
| Çok-worker WSGI | Single-admin kullanım senaryosu; gerek yok |
| Yedek doğrulama (test restore) | Karmaşık altyapı gerektirir; v3+ |
| MFA / 2FA | AD entegrasyonu mevcut; ekstra auth sonraki aşamada |
| Windows ReaR kurulumu | ReaR Linux'a özgü; Windows sadece Ansible ile yönetilir |

---

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BUG-01 | Phase 1 | Pending |
| BUG-02 | Phase 1 | Pending |
| BUG-03 | Phase 1 | Pending |
| BUG-04 | Phase 1 | Pending |
| REF-01 | Phase 2 | Pending |
| REF-02 | Phase 2 | Pending |
| REF-03 | Phase 2 | Pending |
| TEST-01 | Phase 3 | Pending |
| TEST-02 | Phase 3 | Pending |
| TEST-03 | Phase 3 | Pending |
| FEAT-01 | Phase 4 | Pending |
| FEAT-02 | Phase 4 | Pending |
| FEAT-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 13 total
- Mapped to phases: 13
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-17*
*Last updated: 2026-03-17 after initial definition*

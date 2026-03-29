"""Utility helpers — Jinja2 template filters and globals shared across modules."""

import re
import datetime


def cron_describe(minute, hour, dom, month, dow):
    """Cron ifadesini insan okunabilir Türkçe metne çevirir."""
    try:
        m  = str(minute or '*').strip()
        h  = str(hour or '*').strip()
        d  = str(dom or '*').strip()
        mo = str(month or '*').strip()
        dw = str(dow or '*').strip()

        gun_adlari = {
            '0': 'Pazar', '1': 'Pazartesi', '2': 'Salı', '3': 'Çarşamba',
            '4': 'Perşembe', '5': 'Cuma', '6': 'Cumartesi', '7': 'Pazar',
            '1-5': 'Hft içi', '0-4': 'Pzt-Per', '0,6': 'Hft sonu', '6,0': 'Hft sonu',
        }
        ay_adlari = {
            '1': 'Oca', '2': 'Şub', '3': 'Mar', '4': 'Nis',
            '5': 'May', '6': 'Haz', '7': 'Tem', '8': 'Ağu',
            '9': 'Eyl', '10': 'Eki', '11': 'Kas', '12': 'Ara',
        }

        if h.startswith('*/') and m == '0' and d == '*' and mo == '*' and dw == '*':
            return f'Her {h[2:]} saatte'
        if m.startswith('*/') and h == '*' and d == '*' and mo == '*' and dw == '*':
            return f'Her {m[2:]} dakikada'
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw == '*':
            return f'Her gün {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw in gun_adlari:
            return f'Her {gun_adlari[dw]} {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d.isdigit() and mo == '*' and dw == '*':
            return f'Her ay {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d.isdigit() and mo in ay_adlari and dw == '*':
            return f'Her yıl {ay_adlari[mo]} {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        return f'{m} {h} {d} {mo} {dw}'
    except Exception:
        return ''


def safe_dirname(hostname):
    """Hostname'i güvenli bir dizin adına dönüştürür."""
    safe = re.sub(r'[^a-zA-Z0-9_-]', lambda m: '-' if m.group() == '.' else '', hostname)
    safe = re.sub(r'-{2,}', '-', safe)
    safe = safe.strip('-')
    return safe or hostname


def calc_duration(started_at, finished_at):
    """İki tarih string'i arasındaki süreyi insan okunabilir formatta döner."""
    if not started_at or not finished_at:
        return '-'
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        start = datetime.datetime.strptime(str(started_at)[:19], fmt)
        end   = datetime.datetime.strptime(str(finished_at)[:19], fmt)
        secs  = int((end - start).total_seconds())
        if secs < 0:
            return '-'
        if secs < 60:
            return f'{secs}s'
        elif secs < 3600:
            return f'{secs // 60}m {secs % 60}s'
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f'{h}h {m}m'
    except Exception:
        return '-'


def truncate_output(text, max_bytes=1_000_000):
    """Truncate text to max_bytes, appending a marker if truncated.

    Uses byte-level truncation with UTF-8 safety (decode with errors='ignore'
    to avoid splitting multi-byte sequences like Turkish ş/ğ/ç).
    """
    if not text:
        return text

    text_bytes = text.encode('utf-8')
    if len(text_bytes) <= max_bytes:
        return text

    truncated = text_bytes[:max_bytes].decode('utf-8', errors='ignore')
    return truncated + '\n\n[... çıkış 1 MB sınırında kesildi ...]'

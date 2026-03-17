"""
Tests for BUG-03: APScheduler timezone must be configurable via settings page.

These tests verify:
1. init_scheduler() reads timezone from DB settings
2. Default is Europe/Istanbul when not in DB
3. _restart_scheduler_with_timezone() shuts down old scheduler and creates new one
4. Invalid timezone strings are rejected
5. SCHEDULER_TIMEZONES list contains only valid pytz timezones
"""
import pytest
from unittest.mock import MagicMock, patch, call


# ── Tests ────────────────────────────────────────────────────────────────────


def test_timezone_from_db():
    """init_scheduler reads scheduler_timezone from get_settings()."""
    import app as app_module

    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    # Mock DB returning enabled schedules (empty for simplicity)
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []

    with patch.object(app_module, 'get_settings', return_value={'scheduler_timezone': 'America/New_York'}):
        with patch.object(app_module, 'BackgroundScheduler', return_value=mock_scheduler) as mock_bs:
            with patch.object(app_module, 'get_db', return_value=mock_conn):
                with patch.object(app_module, 'HAS_SCHEDULER', True):
                    app_module.init_scheduler()

    mock_bs.assert_called_once_with(timezone='America/New_York', daemon=True)
    mock_scheduler.start.assert_called_once()


def test_timezone_default_when_not_in_db():
    """init_scheduler defaults to Europe/Istanbul when no scheduler_timezone in settings."""
    import app as app_module

    mock_scheduler = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = []

    with patch.object(app_module, 'get_settings', return_value={}):
        with patch.object(app_module, 'BackgroundScheduler', return_value=mock_scheduler) as mock_bs:
            with patch.object(app_module, 'get_db', return_value=mock_conn):
                with patch.object(app_module, 'HAS_SCHEDULER', True):
                    app_module.init_scheduler()

    mock_bs.assert_called_once_with(timezone='Europe/Istanbul', daemon=True)
    mock_scheduler.start.assert_called_once()


def test_scheduler_restart_reloads_jobs():
    """_restart_scheduler_with_timezone shuts down old scheduler, creates new, reloads jobs."""
    import app as app_module

    old_scheduler = MagicMock()
    old_scheduler.running = True

    new_scheduler = MagicMock()

    # Fake schedules in DB
    fake_sched = {
        'id': 1,
        'cron_minute': '0',
        'cron_hour': '2',
        'cron_dom': '*',
        'cron_month': '*',
        'cron_dow': '*',
    }
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [fake_sched]

    with patch.object(app_module, '_scheduler', old_scheduler, create=True):
        with patch.object(app_module, 'BackgroundScheduler', return_value=new_scheduler) as mock_bs:
            with patch.object(app_module, 'get_db', return_value=mock_conn):
                with patch.object(app_module, '_add_scheduler_job') as mock_add_job:
                    app_module._restart_scheduler_with_timezone('UTC')

    # Old scheduler shut down
    old_scheduler.shutdown.assert_called_once_with(wait=False)

    # New scheduler created with new timezone
    mock_bs.assert_called_once_with(timezone='UTC', daemon=True)
    new_scheduler.start.assert_called_once()

    # Jobs re-added for enabled schedules
    mock_add_job.assert_called_once_with(1, '0', '2', '*', '*', '*')


def test_invalid_timezone_rejected():
    """Invalid timezone string raises pytz.exceptions.UnknownTimeZoneError."""
    import pytz
    with pytest.raises(pytz.exceptions.UnknownTimeZoneError):
        pytz.timezone('Invalid/Zone')


def test_scheduler_timezones_list_valid():
    """All entries in SCHEDULER_TIMEZONES are valid pytz timezones."""
    import pytz
    import app as app_module

    for tz in app_module.SCHEDULER_TIMEZONES:
        try:
            pytz.timezone(tz)
        except pytz.exceptions.UnknownTimeZoneError:
            pytest.fail(f"SCHEDULER_TIMEZONES contains invalid timezone: {tz!r}")

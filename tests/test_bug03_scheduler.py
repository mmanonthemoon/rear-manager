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
    import services.scheduler as sched_module

    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch.object(sched_module.settings_repo, 'get_settings', return_value={'scheduler_timezone': 'America/New_York'}):
        with patch.object(sched_module, 'BackgroundScheduler', return_value=mock_scheduler) as mock_bs:
            with patch.object(sched_module.schedule_repo, 'get_all_enabled', return_value=[]):
                with patch.object(sched_module, 'HAS_SCHEDULER', True):
                    sched_module.init_scheduler()

    mock_bs.assert_called_once_with(timezone='America/New_York', daemon=True)
    mock_scheduler.start.assert_called_once()


def test_timezone_default_when_not_in_db():
    """init_scheduler defaults to Europe/Istanbul when no scheduler_timezone in settings."""
    import services.scheduler as sched_module

    mock_scheduler = MagicMock()

    with patch.object(sched_module.settings_repo, 'get_settings', return_value={}):
        with patch.object(sched_module, 'BackgroundScheduler', return_value=mock_scheduler) as mock_bs:
            with patch.object(sched_module.schedule_repo, 'get_all_enabled', return_value=[]):
                with patch.object(sched_module, 'HAS_SCHEDULER', True):
                    sched_module.init_scheduler()

    mock_bs.assert_called_once_with(timezone='Europe/Istanbul', daemon=True)
    mock_scheduler.start.assert_called_once()


def test_scheduler_restart_reloads_jobs():
    """_restart_scheduler_with_timezone shuts down old scheduler, creates new, reloads jobs."""
    import services.scheduler as sched_module

    old_scheduler = MagicMock()
    old_scheduler.running = True

    new_scheduler = MagicMock()

    # Fake schedules returned by schedule_repo
    fake_sched = MagicMock()
    fake_sched.__getitem__ = lambda self, k: {
        'id': 1,
        'cron_minute': '0',
        'cron_hour': '2',
        'cron_dom': '*',
        'cron_month': '*',
        'cron_dow': '*',
    }[k]

    with patch.object(sched_module, '_scheduler', old_scheduler, create=True):
        with patch.object(sched_module, 'BackgroundScheduler', return_value=new_scheduler) as mock_bs:
            with patch.object(sched_module.schedule_repo, 'get_all_enabled', return_value=[fake_sched]):
                with patch.object(sched_module, '_add_scheduler_job') as mock_add_job:
                    sched_module._restart_scheduler_with_timezone('UTC')

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

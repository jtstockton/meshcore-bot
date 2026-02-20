#!/usr/bin/env python3
"""Tests for channel message retry via repeater echo detection."""

import asyncio
import time
import logging
from configparser import ConfigParser
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meshcore import EventType
from modules.models import MeshMessage
from modules.transmission_tracker import TransmissionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**bot_overrides) -> ConfigParser:
    """Build a minimal ConfigParser with [Bot] section."""
    cfg = ConfigParser()
    cfg.add_section('Bot')
    defaults = {
        'rate_limit_seconds': '2',
        'bot_tx_rate_limit_seconds': '0',
        'tx_delay_ms': '0',
        'channel_retry_enabled': 'false',
        'channel_retry_max_attempts': '1',
        'channel_retry_echo_window': '0.05',  # short for tests
    }
    defaults.update(bot_overrides)
    for k, v in defaults.items():
        cfg.set('Bot', k, v)
    return cfg


_SEND_CHAN_MSG_PATH = 'meshcore_cli.meshcore_cli.send_chan_msg'


def _make_bot(config=None, connected=True):
    """Create a mock bot with the attributes CommandManager expects."""
    bot = MagicMock()
    bot.config = config or _make_config()
    bot.connected = connected
    bot.meshcore = MagicMock()
    bot.logger = logging.getLogger('test_bot')

    # Rate limiters that always allow
    bot.rate_limiter = MagicMock()
    bot.rate_limiter.can_send.return_value = True
    bot.rate_limiter.record_send = MagicMock()

    bot.bot_tx_rate_limiter = MagicMock()
    bot.bot_tx_rate_limiter.wait_for_tx = AsyncMock()
    bot.bot_tx_rate_limiter.record_tx = MagicMock()

    # tx_delay_ms is read directly by _apply_tx_delay()
    bot.tx_delay_ms = 0

    # Channel manager
    bot.channel_manager = MagicMock()
    bot.channel_manager.get_channel_number.return_value = 0

    # Transmission tracker
    tracker = MagicMock()
    tracker.record_transmission.return_value = TransmissionRecord(
        timestamp=time.time(),
        content='test',
        target='LongFast',
        message_type='channel',
    )
    tracker.has_repeater_echo.return_value = False
    bot.transmission_tracker = tracker

    return bot


def _make_command_manager(bot):
    """Import and instantiate CommandManager with the plugin loader stubbed out."""
    with patch('modules.command_manager.PluginLoader') as MockLoader:
        MockLoader.return_value.load_all_plugins.return_value = {}
        from modules.command_manager import CommandManager
        return CommandManager(bot)


def _make_send_result(event_type=None):
    """Create a mock result from send_chan_msg."""
    result = MagicMock()
    result.type = event_type or EventType.MSG_SENT
    return result


# ---------------------------------------------------------------------------
# Tests: _send_channel_message_internal
# ---------------------------------------------------------------------------

class TestSendChannelMessageInternal:

    @pytest.mark.asyncio
    async def test_returns_success_and_record(self):
        """Successful send returns (True, TransmissionRecord)."""
        bot = _make_bot()
        cm = _make_command_manager(bot)

        with patch(_SEND_CHAN_MSG_PATH, new_callable=AsyncMock, return_value=_make_send_result()) as _:
            success, record = await cm._send_channel_message_internal('LongFast', 'hello')

        assert success is True
        assert record is not None

    @pytest.mark.asyncio
    async def test_returns_false_when_disconnected(self):
        """Returns (False, None) when bot is disconnected."""
        bot = _make_bot(connected=False)
        cm = _make_command_manager(bot)

        success, record = await cm._send_channel_message_internal('LongFast', 'hello')

        assert success is False
        assert record is None

    @pytest.mark.asyncio
    async def test_returns_false_when_channel_not_found(self):
        """Returns (False, None) when channel name can't be resolved."""
        bot = _make_bot()
        bot.channel_manager.get_channel_number.return_value = None
        cm = _make_command_manager(bot)

        success, record = await cm._send_channel_message_internal('BadChannel', 'hello')

        assert success is False
        assert record is None


# ---------------------------------------------------------------------------
# Tests: send_channel_message (public wrapper – retry spawning)
# ---------------------------------------------------------------------------

class TestSendChannelMessageRetrySpawning:

    @pytest.mark.asyncio
    async def test_no_retry_when_disabled(self):
        """No retry task spawned when channel_retry_enabled is false."""
        bot = _make_bot(config=_make_config(channel_retry_enabled='false'))
        cm = _make_command_manager(bot)

        with patch(_SEND_CHAN_MSG_PATH, new_callable=AsyncMock, return_value=_make_send_result()):
            with patch.object(cm, '_check_channel_echo_and_retry', new_callable=AsyncMock) as mock_retry:
                result = await cm.send_channel_message('LongFast', 'hello')
                await asyncio.sleep(0)  # let any tasks run

        assert result is True
        mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_task_spawned_when_enabled(self):
        """Retry task is spawned when enabled and send succeeds."""
        bot = _make_bot(config=_make_config(channel_retry_enabled='true'))
        cm = _make_command_manager(bot)

        with patch(_SEND_CHAN_MSG_PATH, new_callable=AsyncMock, return_value=_make_send_result()):
            with patch.object(cm, '_check_channel_echo_and_retry', new_callable=AsyncMock) as mock_retry:
                result = await cm.send_channel_message('LongFast', 'hello')
                await asyncio.sleep(0)

        assert result is True
        mock_retry.assert_called_once()
        assert mock_retry.call_args[0][0] == 'LongFast'

    @pytest.mark.asyncio
    async def test_no_retry_on_failed_send(self):
        """No retry task when the send itself fails."""
        bot = _make_bot(config=_make_config(channel_retry_enabled='true'))
        cm = _make_command_manager(bot)

        with patch(_SEND_CHAN_MSG_PATH, new_callable=AsyncMock, return_value=_make_send_result(EventType.ERROR)):
            with patch.object(cm, '_check_channel_echo_and_retry', new_callable=AsyncMock) as mock_retry:
                result = await cm.send_channel_message('LongFast', 'hello')
                await asyncio.sleep(0)

        assert result is False
        mock_retry.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _check_channel_echo_and_retry
# ---------------------------------------------------------------------------

class TestCheckChannelEchoAndRetry:

    def _make_tx_record(self, **overrides):
        defaults = dict(
            timestamp=time.time(), content='hello',
            target='LongFast', message_type='channel',
        )
        defaults.update(overrides)
        return TransmissionRecord(**defaults)

    @pytest.mark.asyncio
    async def test_no_retry_when_echo_detected(self):
        """If repeater echo is detected, no retry happens."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = True
        cm = _make_command_manager(bot)
        record = self._make_tx_record(repeat_count=1, repeater_prefixes={'ab'})

        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock) as mock_send:
            await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=0)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_fires_when_no_echo(self):
        """If no echo detected, retry send is attempted."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
            channel_retry_max_attempts='1',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = False
        cm = _make_command_manager(bot)
        record = self._make_tx_record()

        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock, return_value=(True, None)) as mock_send:
            await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=0)
            mock_send.assert_called_once()
            # Retry should skip user rate limit
            assert mock_send.call_args[1].get('skip_user_rate_limit') is True

    @pytest.mark.asyncio
    async def test_max_attempts_respected(self):
        """Retry does not fire when max attempts already reached."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
            channel_retry_max_attempts='1',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = False
        cm = _make_command_manager(bot)
        record = self._make_tx_record()

        # attempt=1 with max_attempts=1: already used our retry
        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock) as mock_send:
            await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=1)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_retry_when_disconnected(self):
        """Retry is skipped if bot disconnects during the echo window."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
        ))
        bot.connected = False  # disconnected before check
        cm = _make_command_manager(bot)
        record = self._make_tx_record()

        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock) as mock_send:
            await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=0)
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_chains_retry_when_attempts_remain(self):
        """When retry succeeds and attempts remain, another echo check is spawned."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
            channel_retry_max_attempts='2',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = False
        cm = _make_command_manager(bot)
        record = self._make_tx_record()
        new_record = self._make_tx_record()

        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock, return_value=(True, new_record)):
            with patch('asyncio.create_task') as mock_create_task:
                await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=0)
                mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_chain_at_last_attempt(self):
        """When retry succeeds but it's the last attempt, no further echo check."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
            channel_retry_max_attempts='2',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = False
        cm = _make_command_manager(bot)
        record = self._make_tx_record()
        new_record = self._make_tx_record()

        # attempt=1, max=2: this is the last one
        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock, return_value=(True, new_record)):
            with patch('asyncio.create_task') as mock_create_task:
                await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=1)
                mock_create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_send_failure_gracefully(self):
        """Retry send failure is logged but doesn't raise."""
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true', channel_retry_echo_window='0.01',
            channel_retry_max_attempts='1',
        ))
        bot.transmission_tracker.has_repeater_echo.return_value = False
        cm = _make_command_manager(bot)
        record = self._make_tx_record()

        with patch.object(cm, '_send_channel_message_internal', new_callable=AsyncMock, return_value=(False, None)):
            # Should not raise
            await cm._check_channel_echo_and_retry('LongFast', 'hello', None, record, attempt=0)


# ---------------------------------------------------------------------------
# Tests: TransmissionTracker.has_repeater_echo
# ---------------------------------------------------------------------------

class TestHasRepeaterEcho:

    def _make_tracker(self):
        from modules.transmission_tracker import TransmissionTracker
        bot = _make_bot()
        tracker = TransmissionTracker.__new__(TransmissionTracker)
        tracker.bot = bot
        tracker.logger = bot.logger
        return tracker

    def test_no_echo(self):
        record = TransmissionRecord(
            timestamp=time.time(), content='test',
            target='LongFast', message_type='channel', repeat_count=0,
        )
        assert self._make_tracker().has_repeater_echo(record) is False

    def test_has_echo(self):
        record = TransmissionRecord(
            timestamp=time.time(), content='test',
            target='LongFast', message_type='channel',
            repeat_count=1, repeater_prefixes={'ab'},
        )
        assert self._make_tracker().has_repeater_echo(record) is True

    def test_multiple_echoes(self):
        record = TransmissionRecord(
            timestamp=time.time(), content='test',
            target='LongFast', message_type='channel',
            repeat_count=3, repeater_prefixes={'ab', 'cd', 'ef'},
        )
        assert self._make_tracker().has_repeater_echo(record) is True


# ---------------------------------------------------------------------------
# Tests: Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:

    def test_default_config_disabled(self):
        bot = _make_bot()
        cm = _make_command_manager(bot)
        assert cm.channel_retry_enabled is False
        assert cm.channel_retry_max_attempts == 1

    def test_enabled_config(self):
        bot = _make_bot(config=_make_config(
            channel_retry_enabled='true',
            channel_retry_max_attempts='3',
            channel_retry_echo_window='15.0',
        ))
        cm = _make_command_manager(bot)
        assert cm.channel_retry_enabled is True
        assert cm.channel_retry_max_attempts == 3
        assert cm.channel_retry_echo_window == 15.0

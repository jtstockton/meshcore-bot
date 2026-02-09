# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MeshCore Bot is a Python mesh network bot that connects to MeshCore mesh networks via Serial, BLE, or TCP connections. It features a plugin-based command system (40+ commands), background service plugins, a Flask-SocketIO web viewer, and SQLite-backed persistence.

## Running the Bot

```bash
# Install dependencies
pip install -r requirements.txt

# Run with default config
python3 meshcore_bot.py

# Run with custom config
python3 meshcore_bot.py --config /path/to/config.ini

# Run web viewer standalone
python3 -m modules.web_viewer.app
```

### Docker

```bash
docker compose up -d --build
```

### Testing

pytest is listed as a dependency but there is no test suite yet. Test dependencies: `pytest`, `pytest-asyncio`, `pytest-mock`, `pytest-cov`.

## Architecture

### Entry Point & Core

`meshcore_bot.py` → `modules/core.py` (`MeshCoreBot`). The core class orchestrates the entire bot lifecycle: connection management (serial/BLE/TCP), plugin loading, message handling, database initialization, and graceful shutdown via asyncio signal handlers.

### Message Flow

Incoming MeshCore events (contact messages, RF data, adverts) are processed by `modules/message_handler.py`. It correlates RF signal data (SNR/RSSI) with messages using timestamp and pubkey-based indexing. The `modules/command_manager.py` routes messages to the appropriate command plugin based on keyword matching, handles permission checks (DM-only, channel restrictions, banned users), rate limiting, and cooldowns.

### Command Plugin System

All commands live in `modules/commands/` and extend `BaseCommand` from `base_command.py`. The plugin loader (`modules/plugin_loader.py`) dynamically discovers command files at startup.

**Creating a new command:**
```python
# modules/commands/mycommand_command.py
from .base_command import BaseCommand
from ..models import MeshMessage

class MyCommandCommand(BaseCommand):
    name = "mycommand"
    keywords = ['mycommand', 'mc']
    description = "Description here"
    requires_internet = False
    cooldown_seconds = 30

    async def execute(self, message: MeshMessage) -> bool:
        await self.send_response(message, "Response text")
        return True
```

Key `BaseCommand` attributes: `name`, `keywords`, `description`, `requires_dm`, `requires_internet`, `cooldown_seconds`, `category`. Documentation fields for website generation: `short_description`, `usage`, `examples`, `parameters`.

**Plugin overrides:** The `[Plugin_Overrides]` config section maps command names to alternative implementations in `modules/commands/alternatives/`.

### Service Plugin System

Background services live in `modules/service_plugins/` and extend `BaseServicePlugin` from `base_service.py`. They implement async `start()` and `stop()` methods. Current services: Discord Bridge, Weather Service, Packet Capture, Map Uploader.

### Key Modules

| Module | Role |
|--------|------|
| `modules/db_manager.py` | SQLite with table whitelist security, parameterized queries |
| `modules/channel_manager.py` | Channel monitoring and DM handling |
| `modules/repeater_manager.py` | Repeater tracking and contact management |
| `modules/feed_manager.py` | RSS/API feed subscriptions |
| `modules/scheduler.py` | Background message scheduling (runs in separate thread) |
| `modules/mesh_graph.py` | Mesh network topology tracking |
| `modules/i18n.py` | Internationalization via JSON files in `translations/` |
| `modules/security_utils.py` | Input validation and sanitization |
| `modules/rate_limiter.py` | Rate limiting (bot TX, per-user, Nominatim) |
| `modules/utils.py` | Shared utilities, path resolution, formatting |

### Web Viewer

`modules/web_viewer/app.py` is a Flask-SocketIO app providing real-time bot monitoring via WebSocket. Templates in `modules/web_viewer/templates/`, static assets in `modules/web_viewer/static/`.

### Data Model

`MeshMessage` dataclass (`modules/models.py`): `content`, `sender_id`, `sender_pubkey`, `channel`, `hops`, `path`, `is_dm`, `timestamp`, `snr`, `rssi`, `elapsed`.

Protocol enums in `modules/enums.py`: `AdvertFlags`, `PayloadType`, `RouteType`, `DeviceRole`.

## Configuration

Config is INI-based (`config.ini`). See `config.ini.example` for full reference, `config.ini.minimal-example` for minimal setup. Each command and service plugin has its own config section (e.g., `[Wx_Command]`, `[Discord_Bridge]`).

Key sections: `[Connection]` (serial/ble/tcp), `[Bot]` (core behavior), `[Keywords]` (simple keyword→response mappings with template variables), `[Channels]`, `[Logging]`, `[Web_Viewer]`.

## Concurrency Model

The bot is fully async (Python asyncio). Commands, message handlers, and service plugins are all async. The scheduler runs in a background thread coordinating with the async event loop. Flask-SocketIO uses threading mode.

## Localization

10 supported languages via JSON files in `translations/`. Access via `self.translate('commands.wx.usage')` in commands. Fallback to key string if translation is missing.

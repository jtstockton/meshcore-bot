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

Test dependencies are in `pyproject.toml` extras: `pip install -e ".[test]"`. Dependencies: `pytest`, `pytest-asyncio`, `pytest-mock`, `pytest-cov`.

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

Background services live in `modules/service_plugins/` and extend `BaseServicePlugin` from `base_service.py`. They implement async `start()` and `stop()` methods. Services: Discord Bridge, Telegram Bridge, Weather Service, MQTT Weather, Earthquake, Packet Capture, Map Uploader, Webhook, Repeater Prefix Collision, DARC MoWaS.

### Key Modules

| Module | Role |
|--------|------|
| `modules/db_manager.py` | SQLite with table whitelist security, parameterized queries |
| `modules/channel_manager.py` | Channel monitoring and DM handling |
| `modules/repeater_manager.py` | Repeater tracking and contact management |
| `modules/feed_manager.py` | RSS/API feed subscriptions |
| `modules/scheduler.py` | APScheduler-based message scheduling (runs in separate thread) |
| `modules/db_migrations.py` | Versioned schema migrations (additive only, auto-applied on startup) |
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

Key sections: `[Connection]` (serial/ble/tcp, reconnection settings), `[Bot]` (core behavior, rate limits, mention handling), `[Keywords]` (simple keyword→response mappings with template variables), `[Channels]` (monitoring, DMs, flood scopes), `[Admin]` (local admin API), `[Rate_Limits]` (per-channel), `[Webhook]` (inbound HTTP receiver), `[Logging]`, `[Web_Viewer]`.

## Region / Flood Scopes

The bot supports regional flood scopes for controlling which scoped messages it replies to and what scope outgoing messages use.

**Config in `[Channels]`:**
```ini
# Comma-separated list of scopes to reply to. '#' prefix auto-added if omitted.
# Use * to also allow unscoped global flood messages.
flood_scopes = #PSC, #ALW, *

# Fixed scope for proactive sends (scheduled messages, webhooks) when no reply scope is matched.
# outgoing_flood_scope_override = #PSC
```

- Replies automatically mirror the incoming scope (message on `#PSC` gets reply on `#PSC`)
- Scope matching uses HMAC-based verification via RF correlation (`modules/message_handler.py`, `modules/command_manager.py`)
- Individual services (Weather, Earthquake, Discord Bridge, etc.) can override with their own `flood_scope` setting
- Scheduled messages support per-message scopes: `channel:#scope:message` format in `[Scheduled_Messages]`
- Leave `flood_scopes` blank/unset to respond to all messages (default, backward-compatible)

## Deployment Notes

### Git / Branch Strategy
- `origin` remote = upstream (`agessaman/meshcore-bot`) — never push here
- `fork` remote = our fork (`jtstockton/meshcore-bot`)
- `main` branch tracks upstream (never modified locally)
- `prod` branch is the deployment branch, pushed to `fork`

### Deployment-specific changes on prod (vs upstream)
- `docker-compose.yml`: serial device `/dev/ttyUSB0` and web viewer port `8080` uncommented
- `modules/scheduler.py`: `job_defaults={'misfire_grace_time': 60}` added to `BackgroundScheduler` init — prevents scheduled messages from being silently dropped when the scheduler thread wakes up slightly late (default 1s grace time was too tight)
- `CLAUDE.md`: this file (not in upstream)

### Upgrade procedure
1. `git fetch origin` to get latest upstream
2. `git tag prod-backup-<date>` to create rollback point
3. `git reset --hard origin/main` to reset prod to upstream
4. Re-apply the 3 customizations above
5. `docker compose up -d --build` to rebuild
6. `git push fork prod --force-with-lease`

Rollback: `git reset --hard prod-backup-<date> && docker compose up -d --build`

### Scheduled messages format
APScheduler supports 5-field cron keys (`0 9 * * *`) and deprecated HHMM format (`0900`). Prefer cron format for new entries.

### Database
- Volume-mounted at `./data/databases/` — survives code updates
- Schema migrations in `modules/db_migrations.py` auto-apply on startup (additive only, safe)

### Config
- Volume-mounted at `./data/config/:ro` — not part of repo
- Old/unknown config keys are silently ignored via fallback defaults

## Concurrency Model

The bot is fully async (Python asyncio). Commands, message handlers, and service plugins are all async. The scheduler runs in a background thread coordinating with the async event loop. Flask-SocketIO uses threading mode.

## Localization

10 supported languages via JSON files in `translations/`. Access via `self.translate('commands.wx.usage')` in commands. Fallback to key string if translation is missing.

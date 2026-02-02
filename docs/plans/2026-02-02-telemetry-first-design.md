# Telemetry-First: Remove Polling, Add Per-Field Staleness

## Goal

Stop polling the Tesla API for vehicle state. Rely fully on fleet-telemetry streaming, with a 12h safety-net wake poll and per-field "last updated" timestamps in the UI.

## Data Model

Replace `last_telemetry_update: datetime | None` with per-field timestamps:

- `battery_level_updated: datetime | None`
- `charge_state_updated: datetime | None`
- `charge_limit_updated: datetime | None`

`update_from_telemetry()` sets the individual timestamp for each field it receives. `refresh_state()` sets all three timestamps when called.

## Fallback Poll

- Remove startup `refresh_state()` call — wait for telemetry
- Change `WAKE_POLL_INTERVAL` from 2h to 12h
- Staleness check uses the oldest per-field timestamp (or None if never received) to decide if a wake poll is needed
- Remove `last_wake_poll` variable — check oldest field timestamp directly

## Refresh Button

Keep the manual "Refresh State" button. It still calls `refresh_state()`, updates all fields + timestamps. Useful for forcing an update.

## UI

Each status card (Battery, State, Limit) gets a small subtitle showing relative time since last update:

- Format: "2m ago", "1h ago", "3d ago"
- No data yet: "waiting..."
- Older than 12h: orange text as stale indicator

Updated on the existing 30s UI refresh timer.

```
┌─────────┐ ┌──────────────┐ ┌─────────┐
│ Battery │ │ State        │ │ Limit   │
│ 71%     │ │ Disconnected │ │ 50%     │
│ 2m ago  │ │ 14m ago      │ │ 2m ago  │
└─────────┘ └──────────────┘ └─────────┘
```

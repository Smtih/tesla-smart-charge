# Tesla Smart-Charge "Daycare-Proof" Manager — Design

## Problem
Short daily trips (2-minute daycare runs) cause unnecessary micro-cycling of an LFP battery. The Tesla app doesn't support conditional charging logic. LFP batteries need a weekly 100% charge for calibration.

## Solution
A single-file Python app (`main.py`) combining a background charging logic loop with a NiceGUI web dashboard on port 8080.

## Architecture

- **Single process**, two concerns: async background loop + NiceGUI web server
- **Background loop** runs every 30 minutes, evaluates time-based rules, only wakes the car when an action is needed
- **Dashboard** reads shared in-memory state via `ui.timer` (30s refresh)
- **No persistent storage** beyond teslapy's token cache. Stateless on restart.
- **Vehicle selection:** first vehicle on the account (single car)

## Charging Logic (priority order)

1. **Manual Override** — If active and battery == 100%, reset limit to 80% and clear override. Otherwise let it charge.
2. **Friday Push** — Friday >= 20:00: set limit 100%, start charging. Sent once per week (flag resets Monday).
3. **Weekend Hold** — Saturday and Sunday < 22:00: ensure limit is 100%.
4. **Smart Reset** — Sunday >= 22:00: if battery == 100%, reset limit to 80%.
5. **Daycare Protection (Mon-Thu)** — Battery < 50%: set limit to 80% (car starts on its own). Battery >= 50%: no action.

Each rule only wakes the car when it needs to change something.

## Dashboard

- **Status cards:** Battery %, charge state, current limit, active mode
- **Manual override button:** "Charge to 100% Now" with cancel option
- **Action log:** Scrollable timestamped list (100 entries, newest first)

## Error Handling

- All API calls wrapped in try/except
- On failure: log error, skip cycle, retry in 30 minutes
- Dashboard loads even if car is unreachable ("Waiting for first data...")

## Auth

- `teslapy.Tesla('smith.w.da@gmail.com')` with cached token
- First run opens browser for OAuth
- Token refresh handled automatically by teslapy

## Tech Stack

- Python 3.10+
- teslapy (Tesla Fleet API)
- NiceGUI (web dashboard)
- asyncio (background loop)

# Repeating Schedules Design

**Date:** 2026-01-30
**Status:** Implemented

## Overview

Replace hardcoded Friday/Saturday 8pm charging logic with user-configurable repeating weekly schedules. This makes weekend charging behavior explicit, customizable, and smarter (calculates optimal start time based on battery level and charge duration estimates).

## Current Behavior

- **Hardcoded logic**: Friday & Saturday at 8pm, system charges to 100%
- **Fixed timing**: Always starts at 8pm regardless of battery level
- **Weekend-specific modes**: Separate "Overnight Charge" and "Weekend Guard" modes
- **Storage**: `scheduled_charges.json` contains list of datetime strings

## New Behavior

- **Configurable schedules**: User can create repeating weekly schedules via UI
- **Smart timing**: "Done by" logic calculates when to start based on battery level
- **Unified mode system**: Single "Top-Off Guard" runs 7 days/week, "Scheduled Charge" mode for active charges
- **Prefilled defaults**: Saturday & Sunday 8am repeating schedules replace hardcoded Fri/Sat 8pm logic

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage format | Single list with optional `repeat_weekly` flag | Simple, one source of truth, easy to display |
| Regeneration | Immediate (+7 days when charge completes) | Predictable, always see next occurrence |
| Prefilling | First run only (Sat/Sun 8am) | Maintains current behavior, respects user changes |
| Migration | Archive old format, start fresh | Simpler code, acceptable for personal project |
| UI | Simple checkbox for repeat | Minimal UI change, clear intent |
| Visual indicator | Icon (repeat vs schedule) | Clear at a glance which schedules repeat |

## Data Model

### Schedule Object

```python
{
    "time": "2026-02-01T08:00:00",  # ISO format datetime string
    "repeat_weekly": false           # boolean flag (optional, defaults to false)
}
```

### Storage Format

`scheduled_charges.json`:
```json
[
  {"time": "2026-02-01T08:00:00", "repeat_weekly": true},
  {"time": "2026-02-03T20:00:00", "repeat_weekly": false}
]
```

### TeslaManager State Changes

```python
# Change from:
self.scheduled_charges: list[datetime] = []

# To:
self.scheduled_charges: list[dict] = []  # Each: {"time": datetime_str, "repeat_weekly": bool}

# Add new field:
self.active_scheduled_charge: dict | None = None  # Track which schedule is currently charging

# Remove:
self.overnight_done_day: int | None = None  # No longer needed
```

## Mode System

### New Five-Mode System

1. **Not Authenticated** - Sign in required
2. **Manual Override** - User pressed "Charge Now"
3. **Scheduled Charge** - Charging for a scheduled time
4. **Top-Off Guard** - Battery ≥40%, protecting from shallow cycles
5. **Top-Off Guard - Charging** - Battery <40%, limit raised to 75%

### Mode Descriptions

```python
MODE_DESCRIPTIONS = {
    'Not Authenticated': 'Sign in to connect your Tesla account',
    'Manual Override': f'Charging to {WEEKEND_LIMIT}% — user initiated. Will reset to {IDLE_LIMIT}% when battery reaches 100%.',
    'Scheduled Charge': f'Charging to {WEEKEND_LIMIT}% for scheduled time. Will reset to {IDLE_LIMIT}% when complete. Skips if battery ≥{OVERNIGHT_SKIP}%.',
    'Top-Off Guard': f'Protecting battery from shallow cycles. Charge limit at {IDLE_LIMIT}% until battery drops below {CHARGE_TRIGGER}%.',
    'Top-Off Guard - Charging': f'Battery below {CHARGE_TRIGGER}% — limit raised to {WEEKDAY_LIMIT}% for one full charge session.',
}
```

### active_mode Logic

```python
@property
def active_mode(self) -> str:
    if not self.authenticated:
        return 'Not Authenticated'
    if self.manual_override:
        # Distinguish scheduled vs manual
        if self.active_scheduled_charge is not None:
            return 'Scheduled Charge'
        return 'Manual Override'
    # Top-Off Guard (runs all week)
    if self.battery_level is not None and self.battery_level < CHARGE_TRIGGER:
        return 'Top-Off Guard - Charging'
    return 'Top-Off Guard'
```

## Logic Changes

### 1. Schedule Loading & Prefilling

**File:** `main.py`, lines 112-119

**New logic:**
1. Check if `scheduled_charges.json` exists
2. If exists and old format (list of strings) → rename to `.json.old`
3. If no file or old format detected → prefill with Sat/Sun 8am repeating schedules
4. Calculate next Saturday/Sunday 8am from current time
5. Save prefilled schedules

### 2. Schedule Processing in charge_loop

**File:** `main.py`, lines 346-389

**New logic:**
```python
if mgr.scheduled_charges and not mgr.manual_override:
    next_schedule = mgr.scheduled_charges[0]
    next_time = datetime.fromisoformat(next_schedule["time"])
    time_until = (next_time - now).total_seconds() / 60

    # Remove if past
    if time_until < 0:
        mgr.scheduled_charges.pop(0)
        mgr._save_scheduled_charges()

    # Skip if battery high & close to time
    elif mgr.battery_level >= OVERNIGHT_SKIP and time_until <= 30:
        mgr.scheduled_charges.pop(0)
        mgr._save_scheduled_charges()

    # Start charging if needed
    else:
        percent_needed = 100 - mgr.battery_level
        minutes_needed = (percent_needed * 2) + 30
        if time_until <= minutes_needed:
            await mgr.set_charge_limit(WEEKEND_LIMIT)
            await mgr.start_charging()
            mgr.manual_override = True
            mgr.active_scheduled_charge = next_schedule  # Track it!
```

### 3. Completion & Regeneration

**File:** `main.py`, lines 384-399

**New logic:**
```python
if mgr.manual_override and mgr.battery_level >= 100:
    if mgr.active_scheduled_charge:
        schedule = mgr.active_scheduled_charge
        schedule_time = datetime.fromisoformat(schedule["time"])

        # If repeating, create next instance +7 days
        if schedule.get("repeat_weekly", False):
            next_time = schedule_time + timedelta(days=7)
            new_schedule = {
                "time": next_time.isoformat(),
                "repeat_weekly": True
            }
            mgr.scheduled_charges.append(new_schedule)
            mgr.scheduled_charges.sort(key=lambda s: s["time"])

        # Remove completed schedule
        if schedule in mgr.scheduled_charges:
            mgr.scheduled_charges.remove(schedule)
            mgr._save_scheduled_charges()

        mgr.active_scheduled_charge = None

    # Reset to idle
    await mgr.set_charge_limit(IDLE_LIMIT)
    mgr.manual_override = False
```

### 4. Remove Hardcoded Weekend Logic

**Delete:**
- Lines 401-416: Friday/Saturday 8pm hardcoded charging
- Lines 326-327: Monday reset for `overnight_done_day`
- Line 95: `self.overnight_done_day` field

### 5. Simplify Top-Off Guard

**File:** `main.py`, lines 418-462

**Change:**
- Remove separate weekend logic (lines 418-436)
- Remove weekday time check (line 443)
- Merge into single Top-Off Guard block that runs 7 days/week

## UI Changes

### 1. Add "Repeat weekly" Checkbox

**File:** `main.py`, lines 576-626

Add after time input:
```python
repeat_checkbox = ui.checkbox('Repeat weekly').classes('mb-2')
```

Update `on_schedule()`:
```python
new_schedule = {
    "time": dt.isoformat(),
    "repeat_weekly": repeat_checkbox.value
}
mgr.scheduled_charges.append(new_schedule)
mgr.scheduled_charges.sort(key=lambda s: s["time"])

repeat_text = " (repeating weekly)" if repeat_checkbox.value else ""
mgr._log(f'Scheduled: 100% by {dt.strftime("%a %Y-%m-%d %H:%M")}{repeat_text}')

repeat_checkbox.value = False  # Reset after adding
```

### 2. Update Schedule Display

**File:** `main.py`, lines 671-686

Show repeat icon for weekly schedules:
```python
for sc in mgr.scheduled_charges:
    with ui.card().classes('w-full p-2'):
        with ui.row().classes('items-center gap-3 w-full'):
            # Icon: repeat if weekly, schedule if one-time
            icon_name = 'repeat' if sc.get('repeat_weekly', False) else 'schedule'
            icon_color = 'text-blue-600' if sc.get('repeat_weekly', False) else 'text-green-600'
            ui.icon(icon_name).classes(icon_color)

            schedule_time = datetime.fromisoformat(sc["time"])
            ui.label(schedule_time.strftime('%a %Y-%m-%d %H:%M')).classes('text-sm font-mono flex-1')

            ui.button(icon='delete', ...).props('flat dense size=sm').classes('text-red-600')
```

### 3. Remove "How charging works" Expansion

**Delete:** Lines 631-649

The enhanced `MODE_DESCRIPTIONS` now provide this context.

## Migration Strategy

1. On app start, `_load_scheduled_charges()` runs
2. If file exists and contains old format (list of strings):
   - Rename to `scheduled_charges.json.old`
   - Log message: "Archived old schedule format"
3. If no file or old format detected:
   - Calculate next Saturday/Sunday 8am
   - Create prefilled schedules with `repeat_weekly: true`
   - Save to `scheduled_charges.json`

## Testing Considerations

1. **Migration**: Test with existing `scheduled_charges.json` containing datetime strings
2. **Prefilling**: Test fresh install creates Sat/Sun 8am schedules
3. **Regeneration**: Test repeating schedule creates +7 days instance after completion
4. **UI**: Test checkbox creates correct schedule format
5. **Visual**: Test repeat icon shows for weekly schedules
6. **Modes**: Test mode switching (Manual Override vs Scheduled Charge vs Top-Off Guard)
7. **Edge cases**:
   - Schedule time in past
   - Battery already high when scheduling
   - Deleting repeating schedules
   - Multiple repeating schedules

## Implementation Order

1. Update data model (`TeslaManager.__init__`)
2. Update `_load_scheduled_charges()` with migration & prefilling
3. Update `_save_scheduled_charges()` (already correct)
4. Update schedule processing in `charge_loop()`
5. Update completion logic with regeneration
6. Remove hardcoded weekend logic
7. Simplify Top-Off Guard (merge weekend/weekday)
8. Update `active_mode` property
9. Update `MODE_DESCRIPTIONS`
10. Add UI checkbox for repeat
11. Update schedule display with icons
12. Remove "How charging works" expansion
13. Test all scenarios

## Files Modified

- `main.py`: ~200 lines modified/removed
- `scheduled_charges.json`: Format changed (auto-migrated)
- New: `docs/plans/2026-01-30-repeating-schedules-design.md`

## Success Criteria

- ✅ Hardcoded Fri/Sat 8pm logic removed
- ✅ Sat/Sun 8am repeating schedules prefilled on first run
- ✅ Repeating schedules regenerate +7 days after completion
- ✅ UI shows repeat icon for weekly schedules
- ✅ UI checkbox creates repeating schedules
- ✅ Modes distinguish between manual and scheduled charges
- ✅ Old schedule format auto-migrated
- ✅ Top-Off Guard runs 7 days/week

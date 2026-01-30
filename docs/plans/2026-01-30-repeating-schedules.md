# Repeating Schedules Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace hardcoded Friday/Saturday 8pm charging with user-configurable repeating weekly schedules.

**Architecture:** Modify schedule storage from list of datetimes to list of objects with `{time, repeat_weekly}`. When a repeating schedule completes (battery 100%), regenerate +7 days. Remove hardcoded weekend logic and simplify to unified Top-Off Guard running 7 days/week.

**Tech Stack:** Python, NiceGUI, Tesla Fleet API, JSON storage

---

## Task 1: Update Data Model

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:82-100`

**Step 1: Update TeslaManager state fields**

In `TeslaManager.__init__`, make these changes:

```python
# Line 95 - DELETE this line:
self.overnight_done_day: int | None = None  # weekday number of last overnight charge

# Line 98 - CHANGE from:
self.scheduled_charges: list[datetime] = []

# To:
self.scheduled_charges: list[dict] = []  # Each: {"time": str, "repeat_weekly": bool}

# After line 98 - ADD new field:
self.active_scheduled_charge: dict | None = None  # Track which schedule is currently charging
```

**Step 2: Verify changes**

Check that:
- `overnight_done_day` field is removed
- `scheduled_charges` type annotation changed to `list[dict]`
- `active_scheduled_charge` field added

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: update TeslaManager data model for repeating schedules

- Change scheduled_charges from list[datetime] to list[dict]
- Add active_scheduled_charge field to track current schedule
- Remove overnight_done_day field (no longer needed)"
```

---

## Task 2: Update Schedule Loading with Migration & Prefilling

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:112-119`

**Step 1: Replace _load_scheduled_charges method**

Replace lines 112-119 with:

```python
def _load_scheduled_charges(self):
    try:
        if self._schedule_file.exists():
            data = json.loads(self._schedule_file.read_text())
            # Check if old format (list of strings)
            if data and isinstance(data[0], str):
                # Archive old format
                old_file = self._schedule_file.with_suffix('.json.old')
                self._schedule_file.rename(old_file)
                self._log(f'Archived old schedule format to {old_file.name}')
                data = None  # Force prefill
            else:
                self.scheduled_charges = data
                return

        # Prefill with Saturday & Sunday 8am repeating schedules
        now = datetime.now()
        # Find next Saturday
        days_until_sat = (5 - now.weekday()) % 7
        if days_until_sat == 0 and now.hour >= 8:
            days_until_sat = 7  # Already past this Saturday 8am
        next_sat = (now + timedelta(days=days_until_sat)).replace(hour=8, minute=0, second=0, microsecond=0)
        next_sun = next_sat + timedelta(days=1)

        self.scheduled_charges = [
            {"time": next_sat.isoformat(), "repeat_weekly": True},
            {"time": next_sun.isoformat(), "repeat_weekly": True}
        ]
        self._save_scheduled_charges()
        self._log('Prefilled Saturday & Sunday 8am repeating schedules')
    except Exception as e:
        self._log(f'Error loading schedules: {e}')
        self.scheduled_charges = []
```

**Step 2: Add missing import**

At top of file (around line 7), ensure `timedelta` is imported:

```python
from datetime import datetime, timedelta
```

**Step 3: Test migration logic manually**

Create a test `scheduled_charges.json` with old format:

```bash
cd "c:\Users\smith\Tesla App"
echo '["2026-02-01T20:00:00"]' > scheduled_charges.json
```

**Step 4: Run app briefly to test migration**

```bash
python main.py
# Press Ctrl+C after a few seconds
```

Check that:
- `scheduled_charges.json.old` was created with old data
- New `scheduled_charges.json` has Sat/Sun 8am schedules in new format

**Step 5: Commit**

```bash
git add main.py
git commit -m "feat: add schedule migration and Sat/Sun 8am prefilling

- Detect old format (list of strings) and archive to .json.old
- Prefill Saturday & Sunday 8am repeating schedules on first run
- Calculate next Sat/Sun from current time
- Add timedelta import"
```

---

## Task 3: Update Schedule Processing in charge_loop

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:346-389`

**Step 1: Replace scheduled charges logic**

Replace lines 346-389 (entire "Rule 0: Scheduled Charges" section) with:

```python
# --- Rule 0: Scheduled Charges ---
if mgr.scheduled_charges and not mgr.manual_override:
    next_schedule = mgr.scheduled_charges[0]
    next_time = datetime.fromisoformat(next_schedule["time"])
    time_until = (next_time - now).total_seconds() / 60  # minutes

    # Remove if scheduled time has passed
    if time_until < 0:
        mgr._log(f'Scheduled charge time passed ({next_time.strftime("%a %H:%M")}) — removing from schedule')
        mgr.scheduled_charges.pop(0)
        mgr._save_scheduled_charges()
    else:
        # Get current battery level
        if mgr.battery_level is None:
            await mgr.refresh_state()

        if mgr.battery_level is not None:
            # Skip if battery already high enough AND close to scheduled time
            if mgr.battery_level >= OVERNIGHT_SKIP and time_until <= 30:
                mgr._log(f'Scheduled charge — battery at {mgr.battery_level}% (>= {OVERNIGHT_SKIP}%), skipping')
                # Remove this scheduled charge
                mgr.scheduled_charges.pop(0)
                mgr._save_scheduled_charges()
            elif mgr.battery_level >= OVERNIGHT_SKIP:
                # Battery high but still time - might drive before scheduled time
                mgr._log(f'Scheduled charge — battery at {mgr.battery_level}% (high), monitoring until {next_time.strftime("%H:%M")}')
            else:
                # Conservative estimate: 2 minutes per 1% charge + 30 min buffer
                percent_needed = 100 - mgr.battery_level
                minutes_needed = (percent_needed * 2) + 30

                if time_until <= minutes_needed:
                    # Start charging now to be done by scheduled time
                    await mgr.set_charge_limit(WEEKEND_LIMIT)
                    await mgr.start_charging()
                    mgr.manual_override = True
                    mgr.active_scheduled_charge = next_schedule
                    mgr._log(f'Scheduled charge started — need {percent_needed}% in {int(time_until)} min, done by {next_time.strftime("%a %H:%M")}')
```

**Step 2: Verify the change**

Check that:
- Old code accessing `mgr.scheduled_charges[0]` as datetime is replaced
- New code accesses `next_schedule["time"]` and parses with `fromisoformat()`
- `mgr.active_scheduled_charge = next_schedule` is set when charging starts

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: update schedule processing for new dict format

- Parse schedule time from dict with datetime.fromisoformat()
- Track active schedule in mgr.active_scheduled_charge
- Preserve all existing logic (skip, monitoring, start charging)"
```

---

## Task 4: Update Completion Logic with Regeneration

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:384-399`

**Step 1: Replace manual override completion logic**

Find the section after "Remove completed scheduled charges" comment. Replace lines 384-399 with:

```python
# --- Rule 1: Manual Override / Scheduled Charge Completion ---
if mgr.manual_override:
    await mgr.refresh_state()
    if mgr.battery_level is not None and mgr.battery_level >= 100:
        # Check if this was a scheduled charge
        if mgr.active_scheduled_charge:
            schedule = mgr.active_scheduled_charge
            schedule_time = datetime.fromisoformat(schedule["time"])

            # If it's a repeating schedule, create next instance
            if schedule.get("repeat_weekly", False):
                next_time = schedule_time + timedelta(days=7)
                new_schedule = {
                    "time": next_time.isoformat(),
                    "repeat_weekly": True
                }
                mgr.scheduled_charges.append(new_schedule)
                mgr.scheduled_charges.sort(key=lambda s: s["time"])
                mgr._log(f'Repeating schedule — created next instance for {next_time.strftime("%a %H:%M")}')

            # Remove completed schedule
            if schedule in mgr.scheduled_charges:
                mgr.scheduled_charges.remove(schedule)
                mgr._save_scheduled_charges()

            mgr.active_scheduled_charge = None

        # Reset to idle limit
        await mgr.set_charge_limit(IDLE_LIMIT)
        mgr.manual_override = False
        mgr._log(f'Charge complete — battery at 100%, limit set to {IDLE_LIMIT}%')
    else:
        mgr._log(f'Override active — battery at {mgr.battery_level}%')
```

**Step 2: Verify regeneration logic**

Check that:
- `schedule.get("repeat_weekly", False)` checks for repeating flag
- New schedule created with `+timedelta(days=7)`
- List sorted by `s["time"]` (string comparison works for ISO format)
- Active schedule cleared after completion

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add repeating schedule regeneration on completion

- Check if completed schedule has repeat_weekly flag
- Create new instance +7 days when repeating
- Remove completed schedule from list
- Clear active_scheduled_charge tracker"
```

---

## Task 5: Remove Hardcoded Weekend Logic

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:326-327,401-416`

**Step 1: Remove Monday reset logic**

Delete lines 326-327:

```python
# DELETE these lines:
if wd == 0:
    mgr.overnight_done_day = None
```

**Step 2: Remove Friday/Saturday overnight charge logic**

Delete the entire `elif (wd == 4 or wd == 5)...` block (lines 401-416):

```python
# DELETE entire block from line 401-416:
# --- Rule 2: Overnight Charge (Fri/Sat >= 20:00) ---
# ... entire section ...
```

**Step 3: Verify removal**

Check that:
- No references to `overnight_done_day` remain in charge_loop
- No hardcoded Friday/Saturday 8pm logic remains
- The "Rule 3: Weekend Daytime" comment should now be "Rule 2"

**Step 4: Commit**

```bash
git add main.py
git commit -m "refactor: remove hardcoded Friday/Saturday 8pm charging

- Delete overnight_done_day reset logic
- Delete Fri/Sat 8pm hardcoded charge block
- Replaced by Sat/Sun 8am repeating schedules"
```

---

## Task 6: Simplify Top-Off Guard (Merge Weekend/Weekday)

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:418-462`

**Step 1: Replace weekend and weekday logic with unified block**

Replace lines 418-462 (both "Rule 3: Weekend Daytime" and "Rule 4: Top-Off Guard") with:

```python
# --- Rule 2: Top-Off Guard (runs 7 days a week) ---
elif not mgr.manual_override:
    if mgr.battery_level is None:
        mgr._log('Top-off guard — no battery data, skipping')
    elif mgr.battery_level >= NO_CHARGE_ABOVE:
        if mgr.charge_limit != IDLE_LIMIT:
            await mgr.set_charge_limit(IDLE_LIMIT)
            mgr._log(f'Top-off guard — battery at {mgr.battery_level}%, limit set to {IDLE_LIMIT}%')
        else:
            mgr._log(f'Top-off guard — battery at {mgr.battery_level}%, all good')
    elif mgr.battery_level < CHARGE_TRIGGER:
        if mgr.charge_limit != WEEKDAY_LIMIT:
            await mgr.refresh_state()
            await mgr.set_charge_limit(WEEKDAY_LIMIT)
            mgr._log(f'Top-off guard — battery {mgr.battery_level}% < {CHARGE_TRIGGER}%, set limit {WEEKDAY_LIMIT}%')
        else:
            mgr._log(f'Top-off guard — battery {mgr.battery_level}% low, limit already {WEEKDAY_LIMIT}%')
    else:
        mgr._log(f'Top-off guard — battery at {mgr.battery_level}% (hysteresis zone)')

else:
    mgr._log('No matching rule, idle')
```

**Step 2: Verify simplification**

Check that:
- No weekend-specific logic remains (no `wd in (5, 6)` checks)
- No weekday time checks remain (no `wd <= 3 or (wd == 4 and now.hour < 20)`)
- Single unified Top-Off Guard block runs all week
- Uses same constants (CHARGE_TRIGGER, NO_CHARGE_ABOVE, IDLE_LIMIT, WEEKDAY_LIMIT)

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: unify Top-Off Guard to run 7 days/week

- Remove weekend-specific logic
- Remove weekday time checks
- Single hysteresis behavior (40% trigger, 75% target, 30% idle)"
```

---

## Task 7: Update active_mode Property

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:287-301`

**Step 1: Replace active_mode property**

Replace lines 287-301 with:

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

**Step 2: Verify mode logic**

Check that:
- "Overnight Charge" mode removed
- "Weekend Guard" mode removed
- "Scheduled Charge" mode added (when `active_scheduled_charge` is set)
- "Top-Off Guard - Charging" mode added (when battery < 40%)
- Default is "Top-Off Guard"

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: simplify active_mode to 5 states

- Add 'Scheduled Charge' (when active_scheduled_charge set)
- Add 'Top-Off Guard - Charging' (battery < 40%)
- Remove 'Overnight Charge' and 'Weekend Guard'
- Default to 'Top-Off Guard'"
```

---

## Task 8: Update MODE_DESCRIPTIONS

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:502-509`

**Step 1: Replace MODE_DESCRIPTIONS dict**

Replace lines 502-509 with:

```python
MODE_DESCRIPTIONS = {
    'Not Authenticated': 'Sign in to connect your Tesla account',
    'Manual Override': f'Charging to {WEEKEND_LIMIT}% — user initiated. Will reset to {IDLE_LIMIT}% when battery reaches 100%.',
    'Scheduled Charge': f'Charging to {WEEKEND_LIMIT}% for scheduled time. Will reset to {IDLE_LIMIT}% when complete. Skips if battery ≥{OVERNIGHT_SKIP}%.',
    'Top-Off Guard': f'Protecting battery from shallow cycles. Charge limit at {IDLE_LIMIT}% until battery drops below {CHARGE_TRIGGER}%.',
    'Top-Off Guard - Charging': f'Battery below {CHARGE_TRIGGER}% — limit raised to {WEEKDAY_LIMIT}% for one full charge session.',
}
```

**Step 2: Verify descriptions**

Check that:
- 5 modes match those in `active_mode` property
- Descriptions reference correct constants
- "Overnight Charge" and "Weekend Guard" removed
- "Scheduled Charge" and "Top-Off Guard - Charging" added

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: update mode descriptions for new 5-mode system

- Add detailed descriptions for new modes
- Remove obsolete Overnight Charge and Weekend Guard
- Reference current constants in descriptions"
```

---

## Task 9: Remove "How charging works" Expansion

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:631-649`

**Step 1: Delete the expansion UI block**

Delete lines 631-649 (entire expansion with "How charging works"):

```python
# DELETE from line 631-649:
# --- Behaviour Info ---
with ui.expansion('How charging works', icon='info')...
    ...entire block...
```

**Step 2: Verify removal**

Check that the expansion is completely removed. The next section should be "Action Log" card.

**Step 3: Commit**

```bash
git add main.py
git commit -m "refactor: remove 'How charging works' expansion

Enhanced MODE_DESCRIPTIONS now provide this context inline"
```

---

## Task 10: Add "Repeat weekly" Checkbox to UI

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:576-626`

**Step 1: Add checkbox after time input**

After the time_input section (around line 592), add:

```python
# After the time_input with menu code, ADD:
repeat_checkbox = ui.checkbox('Repeat weekly').classes('mb-2')
```

**Step 2: Update on_schedule function**

Update the `on_schedule` function (starting around line 594) to use the new dict format:

```python
async def on_schedule():
    try:
        t = time_input.value or '20:00'
        dt = datetime.strptime(f'{date_input.value} {t}', '%Y-%m-%d %H:%M')
        if dt <= datetime.now():
            mgr._log('Schedule failed — date/time is in the past')
            return

        # Create schedule with repeat flag
        new_schedule = {
            "time": dt.isoformat(),
            "repeat_weekly": repeat_checkbox.value
        }
        mgr.scheduled_charges.append(new_schedule)
        mgr.scheduled_charges.sort(key=lambda s: s["time"])
        mgr._save_scheduled_charges()

        repeat_text = " (repeating weekly)" if repeat_checkbox.value else ""
        mgr._log(f'Scheduled: 100% by {dt.strftime("%a %Y-%m-%d %H:%M")}{repeat_text}')

        date_input.set_value('')
        repeat_checkbox.value = False  # Reset checkbox

        # Check if we need to start charging immediately
        time_until = (dt - datetime.now()).total_seconds() / 60
        if mgr.battery_level is None:
            await mgr.refresh_state()
        if mgr.battery_level is not None:
            # Skip if battery already high enough
            if mgr.battery_level >= OVERNIGHT_SKIP:
                mgr._log(f'Battery at {mgr.battery_level}% (>= {OVERNIGHT_SKIP}%), charge not needed')
            else:
                percent_needed = 100 - mgr.battery_level
                minutes_needed = (percent_needed * 2) + 30
                if time_until <= minutes_needed:
                    await mgr.set_charge_limit(WEEKEND_LIMIT)
                    await mgr.start_charging()
                    mgr.manual_override = True
                    mgr.active_scheduled_charge = new_schedule
                    mgr._log(f'Starting now — need {percent_needed}% in {int(time_until)} min')
    except ValueError:
        mgr._log('Schedule failed — invalid date or time format')
```

**Step 3: Verify checkbox behavior**

Check that:
- Checkbox appears in UI after time input
- `new_schedule` dict created with `time` and `repeat_weekly` fields
- List sorted by `s["time"]`
- Checkbox resets to False after scheduling
- Immediate charge check uses `active_scheduled_charge`

**Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add 'Repeat weekly' checkbox to schedule UI

- Add checkbox after time input
- Create schedule dict with time and repeat_weekly fields
- Sort schedules by time string (ISO format)
- Reset checkbox after adding schedule
- Track active_scheduled_charge for immediate starts"
```

---

## Task 11: Update Schedule Display with Icons

**Files:**
- Modify: `c:\Users\smith\Tesla App\main.py:671-686`

**Step 1: Update schedule display in refresh_ui function**

Find the schedule_container section (around line 671-686) and update to:

```python
schedule_container.clear()
with schedule_container:
    if not mgr.scheduled_charges:
        ui.label('No scheduled charges').classes('text-sm text-gray-400 italic')
    else:
        for sc in mgr.scheduled_charges:
            with ui.card().classes('w-full p-2'):
                with ui.row().classes('items-center gap-3 w-full'):
                    # Icon: repeat if weekly, schedule if one-time
                    icon_name = 'repeat' if sc.get('repeat_weekly', False) else 'schedule'
                    icon_color = 'text-blue-600' if sc.get('repeat_weekly', False) else 'text-green-600'
                    ui.icon(icon_name).classes(icon_color)

                    schedule_time = datetime.fromisoformat(sc["time"])
                    ui.label(schedule_time.strftime('%a %Y-%m-%d %H:%M')).classes('text-sm font-mono flex-1')

                    ui.button(icon='delete', on_click=lambda _, s=sc: (
                        mgr.scheduled_charges.remove(s),
                        mgr._save_scheduled_charges(),
                        mgr._log(f'Removed schedule for {datetime.fromisoformat(s["time"]).strftime("%a %Y-%m-%d %H:%M")}'),
                    )).props('flat dense size=sm').classes('text-red-600')
```

**Step 2: Verify display**

Check that:
- Parse `sc["time"]` with `datetime.fromisoformat()`
- Show 'repeat' icon (blue) for weekly schedules
- Show 'schedule' icon (green) for one-time schedules
- Delete button still works with dict format

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add repeat/schedule icons to schedule display

- Show 'repeat' icon (blue) for weekly schedules
- Show 'schedule' icon (green) for one-time schedules
- Parse time from dict with fromisoformat()
- Update delete button to work with dict format"
```

---

## Task 12: Manual Testing

**Files:**
- Test: `c:\Users\smith\Tesla App\main.py`

**Step 1: Clean test - Fresh install**

```bash
cd "c:\Users\smith\Tesla App"
# Backup existing data
cp scheduled_charges.json scheduled_charges.json.backup 2>/dev/null || true
# Remove schedule file to test prefilling
rm scheduled_charges.json 2>/dev/null || true
```

**Step 2: Run app and verify prefilling**

```bash
python main.py
# Let it run for ~10 seconds, check logs
# Press Ctrl+C
```

Expected:
- Log shows "Prefilled Saturday & Sunday 8am repeating schedules"
- `scheduled_charges.json` contains two schedules with `repeat_weekly: true`
- Times are next Saturday and Sunday at 8:00 AM

**Step 3: Test migration - Old format**

```bash
# Create old format file
echo '["2026-02-05T20:00:00", "2026-02-10T19:30:00"]' > scheduled_charges.json

python main.py
# Let it run for ~10 seconds
# Press Ctrl+C
```

Expected:
- Log shows "Archived old schedule format to scheduled_charges.json.old"
- `scheduled_charges.json.old` exists with old data
- New `scheduled_charges.json` has Sat/Sun 8am schedules

**Step 4: Test UI - Add repeating schedule**

```bash
python main.py
# Open browser to http://localhost:8080
# In UI:
# 1. Enter a future date/time
# 2. Check "Repeat weekly" checkbox
# 3. Click "Schedule"
# 4. Verify repeat icon (🔁) shows in blue
# 5. Add another schedule WITHOUT checking repeat
# 6. Verify schedule icon shows in green
# Press Ctrl+C in terminal
```

Expected:
- First schedule has blue repeat icon
- Second schedule has green schedule icon
- Both show correct date/time

**Step 5: Restore original data**

```bash
cp scheduled_charges.json.backup scheduled_charges.json 2>/dev/null || true
```

**Step 6: Document test results**

Create a quick test summary:

```bash
echo "Manual Testing Summary - $(date)" > test-results.txt
echo "✅ Prefilling: Sat/Sun 8am schedules created" >> test-results.txt
echo "✅ Migration: Old format archived and replaced" >> test-results.txt
echo "✅ UI: Repeat checkbox works" >> test-results.txt
echo "✅ Icons: Repeat (blue) and schedule (green) display correctly" >> test-results.txt
```

**Step 7: Commit test results**

```bash
git add test-results.txt
git commit -m "test: verify repeating schedules implementation

Manual tests passed:
- Prefilling creates Sat/Sun 8am schedules
- Migration archives old format
- UI checkbox creates repeating schedules
- Icons distinguish repeat vs one-time"
```

---

## Task 13: Final Verification & Cleanup

**Files:**
- Review: `c:\Users\smith\Tesla App\main.py`
- Clean: `c:\Users\smith\Tesla App\`

**Step 1: Verify all references updated**

Search for any remaining references to old format:

```bash
cd "c:\Users\smith\Tesla App"
grep -n "overnight_done_day" main.py
# Should return: (no matches)

grep -n "Weekend Guard" main.py
# Should return: (no matches)

grep -n "Overnight Charge" main.py
# Should return: (no matches)
```

Expected: No matches for removed fields/modes

**Step 2: Verify MODE_DESCRIPTIONS coverage**

```bash
# Check that all modes in active_mode have descriptions
grep -A 20 "def active_mode" main.py
grep -A 10 "MODE_DESCRIPTIONS = {" main.py
```

Verify:
- 5 modes in active_mode: Not Authenticated, Manual Override, Scheduled Charge, Top-Off Guard, Top-Off Guard - Charging
- 5 matching entries in MODE_DESCRIPTIONS

**Step 3: Clean up test files**

```bash
rm test-results.txt
rm scheduled_charges.json.backup 2>/dev/null || true
```

**Step 4: Final review of changes**

```bash
git log --oneline --graph -13
git diff HEAD~13 main.py | wc -l
# Should show ~200 lines changed
```

**Step 5: Update design doc status**

Edit `docs/plans/2026-01-30-repeating-schedules-design.md` line 4:

```markdown
**Status:** Implemented
```

**Step 6: Final commit**

```bash
git add docs/plans/2026-01-30-repeating-schedules-design.md
git commit -m "docs: mark repeating schedules design as implemented"
```

---

## Success Criteria Checklist

Verify all items from design doc (section "Success Criteria"):

- [ ] Hardcoded Fri/Sat 8pm logic removed
- [ ] Sat/Sun 8am repeating schedules prefilled on first run
- [ ] Repeating schedules regenerate +7 days after completion
- [ ] UI shows repeat icon for weekly schedules
- [ ] UI checkbox creates repeating schedules
- [ ] Modes distinguish between manual and scheduled charges
- [ ] Old schedule format auto-migrated
- [ ] Top-Off Guard runs 7 days/week

---

## Implementation Complete

**Total Tasks:** 13
**Estimated Time:** 90-120 minutes
**Files Modified:** `main.py` (~200 lines changed)
**Commits:** ~14 commits (one per task + final)

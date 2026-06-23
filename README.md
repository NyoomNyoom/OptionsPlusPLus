# MX Master 4 Actions Ring → Custom Radial Menu

A self-hosted replacement for the Logitech Actions Ring, for environments where
**Logi Options+ cannot be installed** (e.g. enterprise-locked machines). It talks
to the mouse directly over the HID++ protocol, diverts the Actions Ring button to
software, and shows your own radial menu to launch apps, open URLs/folders, and
send keyboard shortcuts.

No Options+. No installer. Just Python + a few pip packages.

---

## ⚠️ Read this first: setup is per-machine

Three values in `ring_menu.py` are **specific to each person's machine and to the
order their mouse was paired to the receiver**. They are almost certainly
different for you than for whoever shared this:

| Value          | What it is                                   | Example |
| -------------- | -------------------------------------------- | ------- |
| `TARGET_COL`   | which receiver interface carries HID++       | `Col02` |
| `DEVICE_INDEX` | which paired slot your mouse occupies (1–6)  | `0x02`  |
| `RING_CID`     | the control ID of the Actions Ring button    | `0x01A0`|

You must run the diagnostic scripts below to discover **your** values, then patch
them into `ring_menu.py`. This README is written so you can hand it to an AI
coding agent (Claude, etc.) and have it walk you through the whole thing.

---

## 🤖 Instructions for the AI agent

> **Agent: follow this sequence with the user. Do not skip the probe steps — the
> hardcoded values in `ring_menu.py` are for a different machine and will fail
> silently (timeouts) if used as-is. Your job is to derive this user's three
> values empirically, then patch them in.**

### Step 0 — Prerequisites
Confirm with the user:
- They have a **Logitech MX Master 4** connected via a **Logi Bolt receiver**
  (USB dongle). If they use **Bluetooth** instead, the transport differs and the
  probe may come back fully silent — note this; it likely means BLE GATT rather
  than classic HID, which these scripts do not handle.
- Python 3.11+ is available. Then have them run:
  ```
  pip install hidapi pyside6 keyboard
  ```
  If `pip` itself is blocked, stop — the policy is stricter than just a blocked
  installer and this approach won't clear it.

### Step 1 — Find the working interface + device index
Have the user run `probe2.py` and paste the full output.

Look for the line marked `<-- WORKS`. Extract from the "WORKING COMBO" summary:
- the **collection** from the winning interface's path (e.g. `Col02` — it's the
  `&Col0X` token in the Windows `path=` string), and
- the **DEVICE_INDEX** that answered (e.g. `0x02`).

If **every** line says `(silent)`: the receiver isn't passing HID++ through.
Likely causes, in order: Bluetooth not Bolt; an enterprise HID filter; or the
mouse is asleep (have them click it first, then re-run). If still silent, the
fallback is a USB capture of Options+ on a personal machine — tell the user this
is a bigger effort and offer to guide it separately.

Patch `TARGET_COL` and `DEVICE_INDEX` in **both** `enumerate2.py` and
`ring_menu.py` with the discovered values before continuing.

### Step 2 — Find the Actions Ring's control ID (CID)
Have the user run `enumerate2.py` and paste the table.

Most CIDs are standard (`0x50` left, `0x51` right, `0x52` middle, `0x53` back,
`0x56` forward, `0xC3` gesture, `0xC4` SmartShift). Look at the line(s) marked
`??? <-- investigate`. On the reference machine the Actions Ring was **`0x01A0`**
with flags `0x31`. The user's may match or differ slightly by firmware.

If there are multiple unknown CIDs, proceed to Step 3 to identify which is the
ring. If `0x01A0` is present, it is very likely the ring — but still verify.

### Step 3 — Confirm which CID is the ring
Edit `identify.py` so `UNKNOWN_CIDS` lists the unknown CIDs from Step 2 (and set
its `TARGET_COL`/`DEVICE_INDEX` to match). Have the user run it and **press the
Actions Ring** (the button under the thumb where the ring rests).

- The CID that prints `PRESSED: 0x....` when they press the ring **is** `RING_CID`.
- Note: on the reference unit, the separate new thumb button did **not** emit an
  event (it uses flags `0xa0` and isn't divertable via `0x1B04`). If the user's
  ring also doesn't print anything, that's the hard case — the ring may be behind
  a private HID++ feature on their firmware; flag it and stop.

Have them run `py identify.py --undo` afterward to restore the buttons.

Patch `RING_CID` in `ring_menu.py` with the confirmed value.

### Step 4 — Configure the menu and run
Edit the `MENU` list at the top of `ring_menu.py`. Each entry is
`("Label", (type, target))` where `type` is one of:
- `"app"` — launch an executable, target is a full path to an `.exe`
- `"folder"` — open a folder in Explorer
- `"url"` — open a URL (or any path `os.startfile` handles)
- `"keys"` — send a shortcut, e.g. `"ctrl+shift+t"` (needs the `keyboard` lib)

Help the user fill in correct paths for **their** machine (their username will
differ — the reference paths contain `JacksonNorth`). Keep it to ~4–8 items.

Then run:
```
py ring_menu.py
```
A tray icon appears. Press-and-hold the ring → menu opens at the cursor → move
toward a wedge → release to fire. Release in the centre "cancel" hole to abort.

### Step 5 — Known rough edges (offer to fix)
- **Wedge alignment:** the angle-to-wedge mapping and label placement are a first
  pass and can look rotated, especially with odd item counts. If it looks off,
  ask how many items they chose and tighten the geometry in `paintEvent` /
  `_track` to match that exact count.
- **Keyboard shortcuts:** `keys` actions inject synthetic input. They may need the
  script run as admin to send into another focused app, and some enterprise
  security tools flag synthetic input. `app`/`folder`/`url` actions are
  stdlib-only and the most reliable — prefer them on locked-down machines.

---

## 📁 Files

| File            | Purpose                                                        |
| --------------- | -------------------------------------------------------------- |
| `probe2.py`     | Sweeps receiver interfaces + indices; finds the working combo. |
| `enumerate2.py` | Lists all reprogrammable controls (CIDs) on the mouse.         |
| `identify.py`   | Diverts unknown CIDs and prints press events to ID the ring.   |
| `ring_menu.py`  | The launcher: HID++ listener + PySide6 radial overlay.         |

Run them in that order on a new machine. Only `ring_menu.py` runs day-to-day.

---

## 🔧 How it works (background)

Logitech mice speak **HID++ 2.0** over the receiver. The Actions Ring is a
*reprogrammable control* exposed by HID++ feature `0x1B04`. We use that feature's
`setCidReporting` function to **divert** the ring press to software instead of its
default action, then listen for the press/release events and drive our own menu.

The MX Master 4 has **no onboard memory**, so the divert is runtime-only and
clears whenever the mouse sleeps or the receiver re-enumerates. `ring_menu.py`'s
listener thread detects those drops and **re-applies the divert automatically**,
so the ring keeps working without intervention.

The radial menu itself is entirely ours (PySide6). We cannot reproduce Logitech's
exact ring UI — that was drawn by Options+ — but since we capture the press, we
can render and act on our own menu, which is the whole point.

---

## ❓ Troubleshooting

- **All probes silent** → Bluetooth not Bolt; mouse asleep (click first);
  enterprise HID filter; or wrong machine entirely.
- **Timeout on a specific request** → wrong `DEVICE_INDEX` or `TARGET_COL`; re-run
  `probe2.py`.
- **Ring opens menu but nothing fires** → check the action path/target exists;
  `keys` may need admin.
- **Menu appears off-centre or wedges misaligned** → cosmetic; tune geometry for
  your item count.
- **Works, then stops after the mouse sleeps** → it should self-recover; watch the
  `[ring]` console messages for "Reconnecting" then "Ring armed".

---

## ⚖️ Note

This interacts with your own hardware over a documented community-reverse-engineered
protocol. It does not bypass any security control — it just configures a mouse. If
your organisation's policy is *only* that the Options+ installer is blocked, this
stays within that line. If raw HID device access or synthetic input is itself
restricted, respect that and talk to IT about whitelisting the Options+ MSI instead.
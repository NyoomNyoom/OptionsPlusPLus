#!/usr/bin/env python3
"""
MX Master 4 Actions Ring -> custom radial menu launcher.

Press-and-hold the Actions Ring (CID 0x01A0): a radial menu appears at the
cursor. While holding, move toward a wedge to highlight it. Release to fire
that wedge's action. Release on the centre (no wedge highlighted) cancels.

Transport (confirmed for this machine):
    Bolt receiver PID 0xC548, Col02 interface, device index 0x02, long reports.

Action types supported:
    ("app",   r"C:\\path\\to\\program.exe")      launch an application
    ("url",   "https://example.com")             open URL or folder path
    ("keys",  "ctrl+shift+t")                    send a keyboard shortcut
    ("folder", r"C:\\Users\\You\\Downloads")     open a folder in Explorer

Dependencies:
    pip install hidapi pyside6 keyboard
    ("keys" actions need 'keyboard'; everything else is stdlib.)

Persistence: the divert is runtime-only and clears when the mouse sleeps or the
receiver re-enumerates. The HID++ thread detects silence/errors and re-applies
the divert automatically, so the ring keeps working across reconnects.
"""

import os
import sys
import time
import threading
import subprocess
import math
import hid
from PySide6 import QtCore, QtGui, QtWidgets

try:
    import keyboard  # only needed for ("keys", ...) actions
    HAVE_KEYBOARD = True
except Exception:
    HAVE_KEYBOARD = False


# ----------------------------------------------------------------------------
# YOUR MENU - edit this. Each entry: ("Label", (type, target)).
# Up to ~8 wedges stays readable; 4-6 is the sweet spot.
# ----------------------------------------------------------------------------
MENU = [
    ("Slack",             ("app",    r"C:\Users\JacksonNorth\AppData\Local\slack\slack.exe")),
    ("Terminal",          ("app",    r"C:\Windows\System32\cmd.exe")),
    ("Confluence",        ("url",    r"https://bll.atlassian.net/wiki/home")),
    ("Arkle",             ("url",    "https://arkle.omnibabble.com/field?date=Live")),
    ("Search Jockey",     ("keys",   "ctrl+space")),
    ("Open Jockey Page",  ("keys",   "ctrl+shift+space")),
    ("Paste Menu",        ("keys",   "win+v")),
]


# ---- HID++ transport (confirmed values) ------------------------------------
LOGITECH_VID = 0x046D
BOLT_PID     = 0xC548
TARGET_COL   = "Col02"
DEVICE_INDEX = 0x02
REPORT_LONG  = 0x11
SOFTWARE_ID  = 0x05
FEAT_IROOT           = 0x0000
FEAT_REPROG_CONTROLS = 0x1B04
RING_CID     = 0x01A0


def find_target_path():
    for d in hid.enumerate(LOGITECH_VID):
        if d.get("usage_page") != 0xFF00:
            continue
        path = d["path"]
        pstr = path.decode("ascii", "replace") if isinstance(path, bytes) else str(path)
        if f"PID_{BOLT_PID:04X}" in pstr.upper() and TARGET_COL in pstr:
            return path
    return None


def _request(dev, feature_index, function_id, params=b""):
    func_sw = ((function_id & 0x0F) << 4) | (SOFTWARE_ID & 0x0F)
    body = bytes([DEVICE_INDEX, feature_index, func_sw]) + params
    body = body.ljust(20, b"\x00")[:20]
    dev.write(bytes([REPORT_LONG]) + body)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        resp = dev.read(64)
        if resp and len(resp) >= 4 and resp[2] == feature_index \
           and (resp[3] & 0x0F) == SOFTWARE_ID:
            return bytes(resp)
        time.sleep(0.01)
    raise TimeoutError("HID++ request timed out")


def _feature_index(dev, feature_id):
    p = bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF])
    return _request(dev, FEAT_IROOT, 0x00, p)[4]


def _set_divert(dev, ridx, cid, on=True):
    flags = 0x03 if on else 0x02   # bit0 = divert, bit1 = "divert field valid"
    p = bytes([(cid >> 8) & 0xFF, cid & 0xFF, flags, 0x00, 0x00, 0x00])
    _request(dev, ridx, 0x03, p)


class RingListener(QtCore.QThread):
    """Background HID++ loop: keep the ring diverted, emit press/release."""
    pressed  = QtCore.Signal()
    released = QtCore.Signal()
    status   = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self._running = True

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            path = find_target_path()
            if not path:
                self.status.emit("Receiver not found - retrying...")
                time.sleep(2.0)
                continue
            dev = hid.device()
            try:
                dev.open_path(path)
                dev.set_nonblocking(True)
                ridx = _feature_index(dev, FEAT_REPROG_CONTROLS)
                _set_divert(dev, ridx, RING_CID, on=True)
                self.status.emit("Ring armed. Press-and-hold to open the menu.")

                ring_down = False
                while self._running:
                    resp = dev.read(64)
                    if resp and len(resp) >= 4 and resp[2] == ridx:
                        cids = set()
                        for j in range(4, 12, 2):
                            c = (resp[j] << 8) | resp[j + 1]
                            if c:
                                cids.add(c)
                        now_down = RING_CID in cids
                        if now_down and not ring_down:
                            ring_down = True
                            self.pressed.emit()
                        elif not now_down and ring_down:
                            ring_down = False
                            self.released.emit()
                    time.sleep(0.004)
            except Exception as e:
                # Sleep/reconnect drops land here -> loop re-arms the divert.
                self.status.emit(f"Reconnecting ({e})")
                time.sleep(1.0)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass


def run_action(action):
    kind, target = action
    try:
        if kind == "app":
            subprocess.Popen([target])
        elif kind == "folder":
            os.startfile(target)
        elif kind == "url":
            os.startfile(target)
        elif kind == "keys":
            if HAVE_KEYBOARD:
                keyboard.send(target)
            else:
                print("keyboard lib not installed; cannot send", target)
    except Exception as e:
        print(f"Action {action} failed: {e}")


class RadialMenu(QtWidgets.QWidget):
    """
    Frameless translucent always-on-top wedge menu drawn at the cursor.

    KEY FIX vs the buggy version: painting and hit-testing now share ONE source
    of truth for wedge angles (`_wedge_bounds`). Previously paintEvent and _track
    used different angle conventions, so the highlighted wedge and the selected
    wedge could disagree near boundaries. They can't anymore.

    Convention used everywhere here:
      * Angles in DEGREES, measured the way Qt's drawPath/arcTo expects:
        0 deg = 3 o'clock, increasing COUNTER-CLOCKWISE.
      * Wedge i occupies [base_i, base_i + span], contiguous, starting at 90 deg
        (12 o'clock) and going counter-clockwise.
      * For hit-testing we convert the cursor to the SAME convention before
        comparing, including flipping screen-Y (which points down).
    """
    RADIUS = 150
    INNER  = 55

    def __init__(self, items):
        super().__init__(None,
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.items = items
        self.center = QtCore.QPointF()
        self.highlight = -1
        self._poll = QtCore.QTimer(self)
        self._poll.timeout.connect(self._track)
        self.hide()

    # --- single source of truth for geometry -------------------------------
    def _span(self):
        return 360.0 / len(self.items)

    def _wedge_base(self, i):
        """Start angle (Qt convention) of wedge i. 12 o'clock, going CCW."""
        # 90 deg = top. Subtract so wedge 0 is centred at the top and
        # subsequent wedges proceed clockwise on screen (feels natural).
        span = self._span()
        return 90.0 - (i + 0.5) * span

    def popup(self):
        c = QtGui.QCursor.pos()
        size = self.RADIUS * 2 + 40
        self.setGeometry(c.x() - size // 2, c.y() - size // 2, size, size)
        self.center = QtCore.QPointF(size / 2, size / 2)
        self.highlight = -1
        self.show()
        self.raise_()
        self._poll.start(16)

    def dismiss_and_fire(self):
        self._poll.stop()
        idx = self.highlight
        self.hide()
        if 0 <= idx < len(self.items):
            from_action = self.items[idx][1]
            # run_action is defined in ring_menu.py's module scope
            run_action(from_action)

    # --- hit-testing: same convention as drawing ---------------------------
    def _angle_to_wedge(self, dx, dy):
        """
        dx, dy are cursor offsets from centre in WIDGET coords (y points down).
        Returns the wedge index under that direction, matching the drawing.
        """
        # Flip y so positive = up, matching Qt's CCW angle convention.
        ang = math.degrees(math.atan2(-dy, dx)) % 360.0
        span = self._span()
        # Wedge i covers (base_i - span, base_i] going clockwise on screen,
        # i.e. ang in [base_i - span, base_i] ... but base decreases with i.
        # Normalise: distance clockwise from wedge 0's leading edge.
        lead0 = (90.0 + 0.5 * span) % 360.0   # leading edge of wedge 0
        # how far clockwise (decreasing angle) we are from lead0
        delta = (lead0 - ang) % 360.0
        idx = int(delta // span)
        if idx >= len(self.items):
            idx = len(self.items) - 1
        return idx

    def _track(self):
        local = self.mapFromGlobal(QtGui.QCursor.pos())
        dx = local.x() - self.center.x()
        dy = local.y() - self.center.y()
        dist = math.hypot(dx, dy)
        if dist < self.INNER:
            new = -1
        else:
            new = self._angle_to_wedge(dx, dy)
        if new != self.highlight:
            self.highlight = new
            self.update()

    # --- drawing: reads the SAME _wedge_base ------------------------------
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        span = self._span()
        rect = QtCore.QRectF(
            self.center.x() - self.RADIUS, self.center.y() - self.RADIUS,
            self.RADIUS * 2, self.RADIUS * 2)

        for i, (label, _action) in enumerate(self.items):
            base = self._wedge_base(i)
            path = QtGui.QPainterPath()
            path.moveTo(self.center)
            path.arcTo(rect, base, span)
            path.closeSubpath()

            if i == self.highlight:
                p.setBrush(QtGui.QColor(60, 130, 230, 235))
            else:
                p.setBrush(QtGui.QColor(35, 35, 40, 205))
            p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 24, 220), 2))
            p.drawPath(path)

            # Label at the wedge's angular midpoint, same convention.
            mid = math.radians(base + span / 2.0)
            tr = (self.RADIUS + self.INNER) / 2.0
            lx = self.center.x() + tr * math.cos(mid)
            ly = self.center.y() - tr * math.sin(mid)   # -sin: screen y is down
            p.setPen(QtGui.QColor(245, 245, 250))
            f = p.font(); f.setPointSize(10); f.setBold(i == self.highlight); p.setFont(f)
            p.drawText(QtCore.QRectF(lx - 55, ly - 14, 110, 28),
                       QtCore.Qt.AlignCenter, label)

        # inner cancel hole
        p.setBrush(QtGui.QColor(15, 15, 18, 230))
        p.setPen(QtGui.QColor(70, 70, 78))
        p.drawEllipse(self.center, self.INNER, self.INNER)
        p.setPen(QtGui.QColor(170, 170, 178))
        p.drawText(QtCore.QRectF(self.center.x() - self.INNER, self.center.y() - 10,
                                 self.INNER * 2, 20),
                   QtCore.Qt.AlignCenter, "cancel")


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    menu = RadialMenu(MENU)
    listener = RingListener()
    listener.pressed.connect(menu.popup)
    listener.released.connect(menu.dismiss_and_fire)
    listener.status.connect(lambda s: print("[ring]", s))
    listener.start()

    # Tray icon so it's not an invisible ghost process.
    tray = QtWidgets.QSystemTrayIcon(
        app.style().standardIcon(QtWidgets.QStyle.SP_DesktopIcon))
    tray.setToolTip("MX Master 4 Ring Menu")
    tmenu = QtWidgets.QMenu()
    quit_action = tmenu.addAction("Quit")
    quit_action.triggered.connect(lambda: (listener.stop(), app.quit()))
    tray.setContextMenu(tmenu)
    tray.show()

    app.exec()
    listener.stop()
    listener.wait(1500)


if __name__ == "__main__":
    main()
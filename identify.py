#!/usr/bin/env python3
"""
Identify which physical button is CID 0x01A0 vs 0x00D7 on the MX Master 4.

Approach: use feature 0x1B04 function 0x03 (setCidReporting) to DIVERT each
unknown control to software. Once diverted, pressing that button makes the
firmware send a divertedButtonsEvent (function 0x00 notification) on feature
0x1B04 containing the CID(s) currently pressed. We listen and print them, so
you can press each mystery button and read off its CID live.

Transport: confirmed Col02 / index 0x02 / long reports.

This is reversible: diverts are runtime-only and clear when the mouse
disconnects/sleeps, or run with --undo to clear them immediately.

Run:  py identify.py          (divert + listen)
      py identify.py --undo   (clear diverts and exit)
"""

import sys
import time
import hid

LOGITECH_VID = 0x046D
BOLT_PID     = 0xC548
TARGET_COL   = "Col02"
DEVICE_INDEX = 0x02
REPORT_LONG  = 0x11
SOFTWARE_ID  = 0x05

FEAT_IROOT           = 0x0000
FEAT_REPROG_CONTROLS = 0x1B04

UNKNOWN_CIDS = [0x01A0, 0x00D7]


def find_target_path():
    for d in hid.enumerate(LOGITECH_VID):
        if d.get("usage_page") != 0xFF00:
            continue
        path = d["path"]
        pstr = path.decode("ascii", "replace") if isinstance(path, bytes) else str(path)
        if f"PID_{BOLT_PID:04X}" in pstr.upper() and TARGET_COL in pstr:
            return path
    raise RuntimeError("Col02 interface not found - is the receiver plugged in?")


def request(dev, feature_index, function_id, params=b""):
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
    raise TimeoutError(f"No response: feature {feature_index:#04x} fn {function_id}")


def get_feature_index(dev, feature_id):
    p = bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF])
    return request(dev, FEAT_IROOT, 0x00, p)[4]


def set_divert(dev, reprog_idx, cid, divert=True):
    # setCidReporting: flags bit0 = divert (and bit? = "divert valid").
    # We set 0x03 = divert ON + valid; 0x02 = divert OFF + valid.
    flags = 0x03 if divert else 0x02
    p = bytes([(cid >> 8) & 0xFF, cid & 0xFF, flags, 0x00, 0x00, 0x00])
    request(dev, reprog_idx, 0x03, p)


def main():
    undo = "--undo" in sys.argv
    path = find_target_path()
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(True)
    try:
        ridx = get_feature_index(dev, FEAT_REPROG_CONTROLS)

        if undo:
            for cid in UNKNOWN_CIDS:
                set_divert(dev, ridx, cid, divert=False)
            print("Diverts cleared. Buttons restored to default behaviour.")
            return

        for cid in UNKNOWN_CIDS:
            set_divert(dev, ridx, cid, divert=True)
            print(f"Diverted CID {cid:#06x} to software.")

        print("\nNow PRESS the new buttons one at a time:")
        print("  - the Actions Ring trigger")
        print("  - the extra thumb button (the new one in front of back/forward)")
        print("Watch which CID prints for each. Ctrl-C when done.\n")

        # Listen for divertedButtonsEvent: feature 0x1B04, function 0x00 notif.
        # Payload after the header is a list of up to 4 currently-pressed CIDs
        # (2 bytes each); all-zero means "released".
        last = None
        while True:
            resp = dev.read(64)
            if resp and len(resp) >= 4 and resp[2] == ridx:
                payload = resp[4:12]
                cids = []
                for j in range(0, 8, 2):
                    c = (payload[j] << 8) | payload[j + 1]
                    if c:
                        cids.append(c)
                state = tuple(cids)
                if state != last:
                    if cids:
                        pretty = ", ".join(f"{c:#06x}" for c in cids)
                        print(f"  PRESSED: {pretty}")
                    else:
                        print("  (released)")
                    last = state
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\nStopping. Run 'py identify.py --undo' to restore the buttons.")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
MX Master 4 control enumerator - configured for the CONFIRMED working transport:
    Bolt receiver (PID 0xC548), Col02 interface, device index 0x02, long reports.

This enumerates every reprogrammable control (feature 0x1B04) so you can find
the CIDs for the standard buttons AND the new MX Master 4 controls
(extra thumb button, Actions Ring).

Read-only. Changes nothing on the mouse. Run:  py enumerate2.py
"""

import time
import hid

LOGITECH_VID = 0x046D
BOLT_PID     = 0xC548
TARGET_COL   = "Col02"        # the receiver's HID++ control channel
DEVICE_INDEX = 0x02           # YOUR mouse: 2nd paired device on the receiver

REPORT_LONG  = 0x11
SOFTWARE_ID  = 0x05

FEAT_IROOT           = 0x0000
FEAT_REPROG_CONTROLS = 0x1B04


def find_target_path():
    """Grab the exact Col02 interface of the Bolt receiver."""
    for d in hid.enumerate(LOGITECH_VID):
        if d.get("usage_page") != 0xFF00:
            continue
        path = d["path"]
        pstr = path.decode("ascii", "replace") if isinstance(path, bytes) else str(path)
        if f"PID_{BOLT_PID:04X}" in pstr.upper() and TARGET_COL in pstr:
            return path
    raise RuntimeError(f"Could not find {TARGET_COL} interface for PID {BOLT_PID:#06x}. "
                       "Is the receiver plugged in?")


def hidpp_request(dev, feature_index, function_id, params=b""):
    func_sw = ((function_id & 0x0F) << 4) | (SOFTWARE_ID & 0x0F)
    body = bytes([DEVICE_INDEX, feature_index, func_sw]) + params
    body = body.ljust(20, b"\x00")[:20]
    dev.write(bytes([REPORT_LONG]) + body)

    deadline = time.time() + 1.0
    while time.time() < deadline:
        resp = dev.read(64)
        if resp:
            if len(resp) >= 4 and resp[2] == feature_index \
               and (resp[3] & 0x0F) == SOFTWARE_ID:
                return bytes(resp)
        time.sleep(0.01)
    raise TimeoutError(f"No response: feature {feature_index:#04x} fn {function_id}")


def get_feature_index(dev, feature_id):
    params = bytes([(feature_id >> 8) & 0xFF, feature_id & 0xFF])
    resp = hidpp_request(dev, FEAT_IROOT, 0x00, params)
    return resp[4]   # 0 = unsupported


KNOWN_CIDS = {
    0x50: "Left click",
    0x51: "Right click",
    0x52: "Middle click",
    0x53: "Back",
    0x56: "Forward",
    0xC3: "Gesture / thumb button",
    0xC4: "SmartShift / mode shift",
    0xD7: "(seen on some MX units)",
}


def main():
    path = find_target_path()
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(True)
    try:
        idx = get_feature_index(dev, FEAT_REPROG_CONTROLS)
        if idx == 0:
            print("Feature 0x1B04 not supported on this device. Unexpected - stop here.")
            return
        print(f"Feature 0x1B04 (reprogrammable controls) at index {idx}.\n")

        count = hidpp_request(dev, idx, 0x00)[4]
        print(f"{count} controls exposed:\n")
        print(f"{'#':>2}  {'CID':>6}  {'TID':>6}  flags  guess")
        print("-" * 56)

        unknown = []
        for i in range(count):
            p = hidpp_request(dev, idx, 0x01, bytes([i]))[4:]
            cid = (p[0] << 8) | p[1]
            tid = (p[2] << 8) | p[3]
            flags = p[4]
            name = KNOWN_CIDS.get(cid, "??? <-- investigate")
            if cid not in KNOWN_CIDS:
                unknown.append(cid)
            print(f"{i:>2}  {cid:#06x}  {tid:#06x}   {flags:02x}   {name}")

        if unknown:
            print("\nUnrecognised CIDs (candidates for Actions Ring / extra button):")
            print("  " + ", ".join(f"{c:#06x}" for c in unknown))
            print("\nTo identify which is which: re-run after physically pressing/")
            print("holding a button is not how 0x1B04 works (it's static info), so")
            print("instead we'll DIVERT each unknown CID one at a time and watch for")
            print("the press event - that's the next script once you see this list.")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
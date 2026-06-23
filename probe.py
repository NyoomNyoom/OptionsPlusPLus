#!/usr/bin/env python3
"""
Bolt-receiver HID++ probe for the MX Master 4 (PID 0xC548).

Fixes vs the last version:
  * Non-blocking reads with a short bounded poll, so a silent index can't hang.
  * Prints each interface's collection so we can see Col01 vs Col02.
  * Focuses on device index 0x01 (first paired device on the receiver) but
    still tries 0xFF as a fallback.
  * Per (interface, index, report-type) it polls briefly then moves on.

Run:  py probe2.py
"""

import time
import hid

LOGITECH_VID = 0x046D
REPORT_SHORT = 0x10
REPORT_LONG  = 0x11
SOFTWARE_ID  = 0x05

IROOT_INDEX  = 0x00
IROOT_PING   = 0x01
PING_MARKER  = 0xAA

# On a Bolt receiver the paired mouse is index 1. Try that first, then fallbacks.
INDICES = (0x01, 0x02, 0xFF)
POLL_SECONDS = 0.30   # total time to wait for a reply per attempt


def list_interfaces():
    out = []
    for d in hid.enumerate(LOGITECH_VID):
        if d.get("usage_page") == 0xFF00:
            out.append(d)
    return out


def try_ping(path, device_index, use_long):
    func_sw = ((IROOT_PING & 0x0F) << 4) | (SOFTWARE_ID & 0x0F)
    body = bytes([device_index, IROOT_INDEX, func_sw, 0x00, 0x00, PING_MARKER])
    if use_long:
        body = body.ljust(20, b"\x00")[:20]
        report = bytes([REPORT_LONG]) + body
    else:
        body = body.ljust(7, b"\x00")[:7]
        report = bytes([REPORT_SHORT]) + body

    dev = hid.device()
    try:
        dev.open_path(path)
        dev.set_nonblocking(True)          # <-- key change: never block
        dev.write(report)
        deadline = time.time() + POLL_SECONDS
        while time.time() < deadline:
            resp = dev.read(64)            # returns [] immediately if nothing
            if resp:
                if len(resp) >= 4 and resp[2] == IROOT_INDEX \
                   and (resp[3] & 0x0F) == SOFTWARE_ID:
                    return bytes(resp)
            time.sleep(0.01)
        return None
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        dev.close()


def main():
    interfaces = list_interfaces()
    print(f"Found {len(interfaces)} candidate 0xFF00 interface(s).\n")

    hit = None
    for n, d in enumerate(interfaces):
        path = d["path"]
        # The collection number is embedded in the Windows path as 'Col0X'.
        pstr = path.decode("ascii", "replace") if isinstance(path, bytes) else str(path)
        col = "Col??"
        for token in pstr.split("&"):
            if "Col" in token:
                col = token.split("#")[0]
        print(f"[interface {n}] usage={d.get('usage'):#06x} {col} "
              f"product={d.get('product_string')!r}")

        for use_long in (True, False):
            kind = "long " if use_long else "short"
            for idx in INDICES:
                resp = try_ping(path, idx, use_long)
                if resp is None:
                    print(f"    {kind} idx={idx:#04x} -> (silent)")
                    continue
                if isinstance(resp, str):
                    print(f"    {kind} idx={idx:#04x} -> {resp}")
                    break
                hexs = " ".join(f"{b:02x}" for b in resp[:8])
                print(f"    {kind} idx={idx:#04x} -> RESPONSE {hexs}  <-- WORKS")
                hit = (n, idx, use_long, path)
        print()

    if hit:
        n, idx, use_long, path = hit
        print("=" * 60)
        print(f"WORKING COMBO: interface {n}, DEVICE_INDEX = {idx:#04x}, "
              f"{'long' if use_long else 'short'} reports")
        print(f"path = {path}")
        print("Plug those into the main remapper script.")
    else:
        print("No combo answered. Next step is a USB capture of Options+ on a")
        print("personal machine to read the real frames. Tell me and I'll guide it.")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
potato_adb_capture.py
adb-only automation for Android emulator / device.
- Dumps UI with uiautomator
- Finds clickable text nodes (short text) as candidate server rows
- Taps each, taps Connect area heuristically, waits, reads vpn IP via `ip -4 addr show`
- Writes potato_vpn_ips.csv with rows: timestamp,server_label,iface,ip,note
Tweak CONNECT_TAP_COORDS, SWIPE logic, and node filters for best results.
"""

import subprocess, time, re, xml.etree.ElementTree as ET, csv
from datetime import datetime

OUTFILE = "potato_vpn_ips.csv"
CONNECT_WAIT = 8        # seconds to wait after tapping connect
DISCONNECT_WAIT = 1
# Fallback coordinate to tap where Connect/Disconnect usually appears on many devices (center-bottom)
CONNECT_TAP_COORDS = (540, 1700)   # (x,y) - adjust if needed for the emulator/resolution
# If server list is paginated, we attempt a simple swipe
SWIPE_FROM = (540, 1600)
SWIPE_TO   = (540, 500)
SWIPE_DURATION_MS = 500

# Helper: run adb commands
def adb(cmd):
    p = subprocess.run(["adb"] + cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.stdout, p.stderr, p.returncode

# Dump UI using uiautomator and pull uidump.xml
def dump_ui():
    adb(["shell", "uiautomator", "dump", "/sdcard/uidump.xml"])
    adb(["pull", "/sdcard/uidump.xml", "."])
    tree = ET.parse("uidump.xml")
    return tree

# Heuristic: find clickable nodes with short text
def find_server_nodes(tree):
    root = tree.getroot()
    nodes = []
    for node in root.iter('node'):
        clickable = node.attrib.get('clickable','false')
        text = (node.attrib.get('text') or "").strip()
        bounds = node.attrib.get('bounds','').strip()
        if clickable == 'true' and text and 1 <= len(text) <= 40:
            # filter out common button-like words
            if text.lower() in ("connect","disconnect","refresh","settings","back","search","ok","cancel"):
                continue
            # likely server entry
            nodes.append((text, bounds))
    return nodes

def bounds_center(bounds):
    m = re.findall(r'\d+', bounds)
    if len(m)>=4:
        x1,y1,x2,y2 = map(int, m[:4])
        return ((x1+x2)//2, (y1+y2)//2)
    return None

def get_vpn_ip():
    out,err,code = adb(["shell","ip","-4","addr","show"])
    if code != 0:
        return None
    cur_iface = None
    for line in out.splitlines():
        line = line.strip()
        m_iface = re.match(r'^\d+:\s+([^:]+):', line)
        if m_iface:
            cur_iface = m_iface.group(1)
            continue
        m_ip = re.search(r'inet\s+([0-9\.]+)/\d+', line)
        if m_ip and cur_iface and re.search(r'(tun|tap|ppp|vpn|wg|utun)', cur_iface, re.I):
            return cur_iface, m_ip.group(1)
    return None

def write_header():
    with open(OUTFILE,"w",newline="") as f:
        csv.writer(f).writerow(["timestamp","server_label","iface","ip","note"])

def append_row(label, iface_ip, note=""):
    ts = datetime.now().isoformat()
    iface, ip = ("","")
    if iface_ip:
        iface, ip = iface_ip
    with open(OUTFILE,"a",newline="") as f:
        csv.writer(f).writerow([ts,label,iface,ip,note])

def tap_coord(x,y):
    adb(["shell","input","tap", str(x), str(y)])

def swipe():
    x1,y1 = SWIPE_FROM
    x2,y2 = SWIPE_TO
    adb(["shell","input","swipe", str(x1), str(y1), str(x2), str(y2), str(SWIPE_DURATION_MS)])

def main():
    write_header()
    # initial dump
    tree = dump_ui()
    nodes = find_server_nodes(tree)
    if not nodes:
        print("No candidate server nodes found on initial dump. Trying one swipe and dump again.")
        swipe()
        time.sleep(0.8)
        tree = dump_ui()
        nodes = find_server_nodes(tree)
    if not nodes:
        print("No clickable server-like nodes found. You may need to tweak node filter or provide coordinates.")
        return
    print(f"Found {len(nodes)} candidate server nodes.")
    seen = set()
    for idx,(txt,bounds) in enumerate(nodes, start=1):
        label = txt or f"item_{idx}"
        if label in seen:
            continue
        seen.add(label)
        center = bounds_center(bounds)
        if not center:
            continue
        x,y = center
        print(f"[{idx}] Tapping '{label}' at {center}")
        tap_coord(x,y)
        time.sleep(0.6)
        # tap Connect heuristically
        print("Tapping Connect area at", CONNECT_TAP_COORDS)
        tap_coord(*CONNECT_TAP_COORDS)
        time.sleep(CONNECT_WAIT)
        vip = get_vpn_ip()
        if vip:
            append_row(label, vip, note="ok")
            print("Got IP:", vip)
        else:
            append_row(label, None, note="no_ip")
            print("No IP for", label)
        # try disconnect (tap same connect area)
        tap_coord(*CONNECT_TAP_COORDS)
        time.sleep(DISCONNECT_WAIT)
        # back to list
        adb(["shell","input","keyevent","4"])
        time.sleep(0.6)
    print("Done. Output:", OUTFILE)

if __name__ == "__main__":
    main()

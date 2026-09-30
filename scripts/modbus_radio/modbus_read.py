#!/usr/bin/env python3
"""modbus_read — read and decode the modbus_radio register map (local tool, stdlib only).

Examples:
  modbus_read.py                         # host from RA2_HOST / .env.ra2, port 1502, everything decoded
  modbus_read.py 192.168.169.169 -p 1502 --json
  modbus_read.py --watch 10              # repeat every 10 s
  modbus_read.py --dump                  # every used register as 'address: value'
  modbus_read.py --raw 10050 30          # 'address: value' for a range (FC 04; --fc 3 for holding)

Register map: README.md next to this file.
"""

import argparse
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

NA_I16, NA_U16, NA_U32 = -0x8000, 0xFFFF, 0xFFFFFFFF
TABLE_STRIDE = 50

ENUMS = {
    "profile": ["?", "2CPFSK 3/4", "4CPFSK 3/4", "4CPFSK 1/1", "DPSK 3/4", "pi/4DQPSK 3/4",
                "16DEQAM 3/4", "64QAM 3/4", "256QAM 3/4", "256QAM 5/6"],
    "modulation": ["?", "2CPFSK", "4CPFSK", "DPSK", "pi/4DQPSK", "16DEQAM", "64QAM", "256QAM"],
    "block_state": ["?", "free", "stepping up", "blocking"],
    "connection": ["?", "up", "down"],
}
SOURCE_STATE = ["ok", "no data yet", "last refresh failed"]
EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
              4: "server device failure"}

# (offset, label, kind) — kinds as in modbus_radio.py
SIG_FIELDS = [
    (1, "IP", "ipv4"), (3, "Link", "link"), (6, "Hdr cnt", "u32"),
    (8, "RSS avg", "i16"), (9, "RSS dev", "i16"), (10, "RSS min", "i16"), (11, "RSS max", "i16"),
    (12, "PhyMSE avg", "i16"), (13, "PhyMSE dev", "i16"), (14, "PhyMSE min", "i16"), (15, "PhyMSE max", "i16"),
    (16, "Freq off", "i16"), (17, "Att1", "u16"),
    (18, "Att2 avg", "i16"), (19, "Att2 min", "i16"), (20, "Att2 max", "i16"),
    (21, "Data cnt", "u32"),
    (23, "DMSE avg", "i16"), (24, "DMSE dev", "i16"), (25, "DMSE min", "i16"), (26, "DMSE max", "i16"),
]
SIG_NA_FIELDS = [
    (1, "Pre-frame count", "u32"),
    (3, "Pre-frame RSS avg [dBm]", "i16"), (4, "Pre-frame RSS dev", "i16"),
    (5, "Pre-frame RSS min [dBm]", "i16"), (6, "Pre-frame RSS max [dBm]", "i16"),
    (7, "Pre-frame Att1 [%]", "u16"),
    (8, "Pre-frame Att2 avg [dB]", "i16"), (9, "Pre-frame Att2 min [dB]", "i16"),
    (10, "Pre-frame Att2 max [dB]", "i16"),
    (20, "Others count", "u32"),
    (22, "Others RSS avg [dBm]", "i16"), (23, "Others RSS dev", "i16"),
    (24, "Others RSS min [dBm]", "i16"), (25, "Others RSS max [dBm]", "i16"),
    (26, "Others PhyMSE avg [dB]", "i16"), (27, "Others PhyMSE dev", "i16"),
    (28, "Others PhyMSE min [dB]", "i16"), (29, "Others PhyMSE max [dB]", "i16"),
]
ACM_FIELDS = [
    (1, "IP", "ipv4"), (3, "Link", "link"), (6, "MC", "hex"),
    (7, "Profile", "enum:profile"), (8, "State", "enum:block_state"), (9, "Conn", "enum:connection"),
    (10, "Fb MSE", "x10"), (11, "RX MSE", "x10"), (12, "Relay MSE", "x10"), (13, "Last meas", "ts"),
]


class ModbusError(Exception):
    pass


class Client:
    def __init__(self, host, port, unit, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.unit, self.tid = unit, 0

    def close(self):
        self.sock.close()

    def _recv(self, n):
        b = bytearray()
        while len(b) < n:
            chunk = self.sock.recv(n - len(b))
            if not chunk:
                raise ModbusError("connection closed by server")
            b += chunk
        return bytes(b)

    def read(self, addr, qty, fc=4):
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHBBHH", self.tid, 0, 6, self.unit, fc, addr, qty))
        tid, _pid, length, _uid = struct.unpack(">HHHB", self._recv(7))
        pdu = self._recv(length - 1)
        if tid != self.tid:
            raise ModbusError(f"transaction id mismatch ({tid} != {self.tid})")
        if pdu[0] & 0x80:
            raise ModbusError(f"exception {pdu[1]} ({EXCEPTIONS.get(pdu[1], '?')}) at {addr}+{qty}")
        return list(struct.unpack(f">{pdu[1] // 2}H", pdu[2:]))

    def read_long(self, addr, qty, fc=4):
        out = []
        while qty:
            n = min(qty, 125)
            out += self.read(addr, n, fc)
            addr, qty = addr + n, qty - n
        return out


# --- decoding ----------------------------------------------------------------------------------

def u32(r, o):
    return (r[o] << 16) | r[o + 1]


def decode(kind, r, o):
    if kind == "u16":
        return None if r[o] == NA_U16 else r[o]
    if kind == "hex":
        return None if r[o] == NA_U16 else f"{r[o]:02X}"
    if kind in ("i16", "x10"):
        v = r[o] - 0x10000 if r[o] & 0x8000 else r[o]
        if v == NA_I16:
            return None
        return v / 10 if kind == "x10" else v
    if kind in ("u32", "ts"):
        v = u32(r, o)
        if v == NA_U32:
            return None
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v)) if kind == "ts" and v else v
    if kind == "ipv4":
        b = struct.pack(">2H", r[o], r[o + 1])
        return ".".join(str(x) for x in b) if any(b) else None
    if kind in ("mac", "link"):
        b = struct.pack(">3H", *r[o:o + 3])
        if not any(b):
            return None
        if kind == "link":                  # short form as the UI shows it: ':20:49:69'
            b = b.lstrip(b"\0") or b"\0"
            return ":" + ":".join(f"{x:02x}" for x in b)
        return ":".join(f"{x:02x}" for x in b)
    if kind.startswith("enum:"):
        names = ENUMS[kind[5:]]
        return names[r[o]] if r[o] < len(names) else f"#{r[o]}"
    raise ValueError(kind)


def ascii_(r, o, n):
    return struct.pack(f">{n}H", *r[o:o + n]).rstrip(b"\0").decode("ascii", "replace")


def ts(v):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v)) if v else None


def read_table(c, base, fields):
    h = c.read(base, 12)
    hdr = {"table_id": h[0], "stride": h[1], "max_records": h[2], "records": h[3], "sequence": h[4],
           "source_state": SOURCE_STATE[h[5]] if h[5] < 3 else h[5], "refreshed": ts(u32(h, 6)),
           "window_from": ts(u32(h, 8)), "window_until": ts(u32(h, 10))}
    rows = []
    n = 1
    while n <= h[3]:
        chunk = c.read(base + TABLE_STRIDE * n, min(2, h[3] - n + 1) * TABLE_STRIDE)
        for k in range(len(chunk) // TABLE_STRIDE):
            rec = chunk[k * TABLE_STRIDE:(k + 1) * TABLE_STRIDE]
            if rec[0] == 1:
                rows.append({"n": n + k, **{label: decode(kind, rec, off) for off, label, kind in fields}})
        n += 2
    return {"header": hdr, "records": rows}


# used registers per block (start, count); table records are added from the record count
USED = [(0, 13), (20, 48), (1000, 9), (1010, 16), (1030, 3), (2000, 11), (2020, 10)]
TABLES = [(10000, 27), (20000, 15)]           # (base, used registers per record)


def dump(c, fc):
    ranges = list(USED)
    for base, used in TABLES:
        count = c.read(base + 3, 1, fc)[0]
        ranges.append((base, 12))
        ranges += [(base + TABLE_STRIDE * n, used) for n in range(1, count + 1)]
    for start, qty in ranges:
        yield from zip(range(start, start + qty), c.read_long(start, qty, fc))


def read_all(c):
    info = c.read(0, 100)
    if info[0] != 0x5241:
        raise ModbusError(f"magic 0x{info[0]:04X} != 0x5241 — not a modbus_radio server")
    radio = c.read(1000, 40)
    na = c.read(2000, 40)
    health = info[6]
    rf_dbm = decode("x10", radio, 6)
    return {
        "info": {
            "map_version": info[1], "uptime_s": u32(info, 2), "device_time": ts(u32(info, 4)),
            "health": {"status_ok": bool(health & 1), "statistics_ok": bool(health & 2),
                       "api_reachable": bool(health & 4)},
            "poll_errors": info[7], "status_interval_s": info[8], "stat_interval_s": info[9],
            "stat_window_s": u32(info, 10), "clients": info[12],
            "firmware": ascii_(info, 20, 16), "station_name": ascii_(info, 36, 32),
        },
        "radio": {
            "valid": radio[0] == 1,
            "baudrate_bd": decode("u32", radio, 1), "mac": decode("mac", radio, 3),
            "rf_power_dbm": rf_dbm, "rf_power_mw": decode("u16", radio, 7),
            "modulation": decode("enum:modulation", radio, 8), "rf_power_text": ascii_(radio, 10, 16),
            "acm_links": radio[30], "acm_up": radio[31], "acm_down": radio[32],
        },
        "signal_na": {"valid": na[0] == 1, **{label: decode(kind, na, off) for off, label, kind in SIG_NA_FIELDS}},
        "signal": read_table(c, 10000, SIG_FIELDS),
        "acm": read_table(c, 20000, ACM_FIELDS),
    }


# --- output ------------------------------------------------------------------------------------

def fmt(v):
    return "-" if v is None else str(v)


def print_table(title, table, fields):
    h = table["header"]
    extra = f", window {h['window_from']} … {h['window_until']}" if h["window_from"] else ""
    print(f"\n== {title}  ({h['records']} record(s), {h['source_state']}, seq {h['sequence']}, "
          f"refreshed {fmt(h['refreshed'])}{extra})")
    if not table["records"]:
        print("   (no records)")
        return
    cols = ["n"] + [label for _, label, _ in fields]
    cells = [[fmt(r[c]) for c in cols] for r in table["records"]]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("   " + "  ".join(c.rjust(w) for c, w in zip(cols, widths)))
    for row in cells:
        print("   " + "  ".join(v.rjust(w) for v, w in zip(row, widths)))


def print_all(d):
    i, r = d["info"], d["radio"]
    h = i["health"]
    print(f"== Info  map v{i['map_version']}  station {i['station_name']!r}  fw {i['firmware']}  "
          f"device time {i['device_time']}  uptime {i['uptime_s']} s  clients {i['clients']}")
    print(f"   health: status {'ok' if h['status_ok'] else 'FAIL'}, statistics "
          f"{'ok' if h['statistics_ok'] else 'FAIL'}, API {'reachable' if h['api_reachable'] else 'UNREACHABLE'}; "
          f"poll errors {i['poll_errors']}; intervals {i['status_interval_s']}/{i['stat_interval_s']} s, "
          f"window {i['stat_window_s']} s")
    print("\n== Radio status (Diagnostics → Information → Radio)")
    if not r["valid"]:
        print("   (no data)")
    else:
        bd = r["baudrate_bd"]
        print(f"   Baudrate       {fmt(bd / 1000 if bd is not None else None)} kBaud ({fmt(bd)} Bd)")
        print(f"   MAC address    {fmt(r['mac'])}")
        print(f"   RF power RMS   {fmt(r['rf_power_dbm'])} dBm / {fmt(r['rf_power_mw'])} mW "
              f"({r['modulation']})   UI text: {r['rf_power_text']!r}")
        print(f"   ACM links      {r['acm_links']} total, {r['acm_up']} up, {r['acm_down']} down")
    print_table("Radio ACM / neighbour links", d["acm"], ACM_FIELDS)
    print_table("Radio signal statistics", d["signal"], SIG_FIELDS)
    na = d["signal_na"]
    print("\n== Radio signal non-addressable statistics")
    if not na["valid"]:
        print("   (no data)")
    else:
        for _, label, _ in SIG_NA_FIELDS:
            print(f"   {label:<26} {fmt(na[label])}")


def default_host():
    if os.environ.get("RA2_HOST"):
        return os.environ["RA2_HOST"]
    for d in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        f = d / ".env.ra2"
        if f.is_file():
            for line in f.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "RA2_HOST":
                    return v.strip().strip("'\"")
    return None


def main():
    p = argparse.ArgumentParser(description="Read and decode the modbus_radio register map")
    p.add_argument("host", nargs="?", help="device address (default: RA2_HOST or .env.ra2)")
    p.add_argument("-p", "--port", type=int, default=1502)
    p.add_argument("-u", "--unit", type=int, default=1, help="Modbus Unit ID (default 1)")
    p.add_argument("-t", "--timeout", type=float, default=5)
    p.add_argument("--json", action="store_true", help="decoded map as JSON")
    p.add_argument("--dump", action="store_true", help="every used register as 'address: value'")
    p.add_argument("--raw", nargs=2, type=int, metavar=("ADDR", "QTY"), help="'address: value' for a range")
    p.add_argument("--fc", type=int, choices=(3, 4), default=4, help="function for --dump/--raw (default 4)")
    p.add_argument("--signed", action="store_true", help="--dump/--raw: show values as int16")
    p.add_argument("--hex", action="store_true", help="--dump/--raw: show values as hex")
    p.add_argument("--watch", type=float, metavar="S", help="repeat every S seconds")
    a = p.parse_args()
    host = a.host or default_host()
    if not host:
        p.error("no host given and RA2_HOST / .env.ra2 not found")
    host = host.split(":")[0] if host.count(":") == 1 else host    # .env.ra2 may hold host:apiport

    while True:
        try:
            c = Client(host, a.port, a.unit, a.timeout)
            try:
                if a.dump or a.raw:
                    pairs = dump(c, a.fc) if a.dump else zip(range(a.raw[0], a.raw[0] + a.raw[1]),
                                                             c.read_long(a.raw[0], a.raw[1], a.fc))
                    for addr, v in pairs:
                        if a.hex:
                            v = f"0x{v:04X}"
                        elif a.signed and v & 0x8000:
                            v -= 0x10000
                        print(f"{addr}: {v}")
                else:
                    d = read_all(c)
                    if a.json:
                        print(json.dumps(d, indent=2))
                    else:
                        print_all(d)
            finally:
                c.close()
        except (OSError, ModbusError) as exc:
            print(f"modbus_read: {host}:{a.port}: {exc}", file=sys.stderr)
            if not a.watch:
                return 1
        if not a.watch:
            return 0
        print(f"\n---- {time.strftime('%H:%M:%S')} (Ctrl+C to stop) ----")
        try:
            time.sleep(a.watch)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())

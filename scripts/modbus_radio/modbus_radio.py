"""modbus_radio — Modbus TCP server mirroring the radio signal statistics and radio status.

Runs inside a RipEx2 / RA2 under the Scripting launcher. A poller thread reads the local unit
over the RA2 API (token mode) and builds a register image; the main thread serves it read-only
over Modbus TCP (FC 03 and FC 04 return the same map). Register map: README.md next to this file.

Instance arguments (argparse):
  --port N               TCP port to listen on (required, e.g. 1502)
  --bind ADDR            listen address (default 0.0.0.0)
  --status-interval S    refresh of radio / radio_acm status (default 60)
  --stat-interval S      refresh of the signal statistics (default 60)
  --stat-window S        statistics window, now - S ... now (default 900)
  --max-clients N        concurrent Modbus clients (default 8)
  --unit-id N            answer only this Unit ID (default: any)
  --api-host HOST        RA2 API host (default localhost; other values are for testing)
  --log-level LEVEL      DEBUG, INFO (default), WARNING, ERROR; DEBUG logs every client and request

Runtime contract (see skills/scripting/SKILL.md): payload first, line buffering, RLIMIT_CPU raised,
SIGTERM ends both loops within ~1 s, one output line per error / state change / 10 minutes.
Output goes through `logging` to stdout (thread-safe, flushed per record, captured by the launcher).
"""

import argparse
import array
import json
import logging
import re
import resource
import selectors
import signal
import socket
import struct
import sys
import threading
import time

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2MgmtError, Ra2SessionExpiredError

MAP_VERSION = 1
MAGIC = 0x5241                      # "RA"

NA_I16 = 0x8000                     # "not available" sentinels
NA_U16 = 0xFFFF
NA_U32 = 0xFFFFFFFF

# Readable address ranges [start, end); anything else answers exception 02.
BLOCKS = [(0, 100), (1000, 1100), (2000, 2100), (10000, 30000)]

INFO_BASE, RADIO_BASE, SIG_NA_BASE = 0, 1000, 2000
TABLE_STRIDE, TABLE_MAX = 50, 199
SIG_TABLE_BASE, SIG_TABLE_ID = 10000, 1
ACM_TABLE_BASE, ACM_TABLE_ID = 20000, 2

SRC_OK, SRC_NO_DATA, SRC_FAILED = 0, 1, 2

# --- field tables: (offset, source key, kind) --------------------------------------------------
# kinds: u16, i16, u32, ipv4, mac, x10 (int16 value*10), hex (uint16 from hex text), ts (uint32),
#        enum:<name>. Add a field = add a line (next reserved offset) and bump MAP_VERSION.

SIG_FIELDS = [
    (1, "IP_address", "ipv4"),
    (3, "Link_address", "mac"),
    (6, "Frame_count", "u32"),
    (8, "Rss_avg", "i16"), (9, "Rss_dev", "i16"), (10, "Rss_min", "i16"), (11, "Rss_max", "i16"),
    (12, "Subhdr_mse_avg", "i16"), (13, "Subhdr_mse_dev", "i16"),
    (14, "Subhdr_mse_min", "i16"), (15, "Subhdr_mse_max", "i16"),
    (16, "Freq_offset", "i16"),
    (17, "Att_att1", "u16"),
    (18, "Att_att2_avg", "i16"), (19, "Att_att2_min", "i16"), (20, "Att_att2_max", "i16"),
    (21, "Data_count", "u32"),
    (23, "Data_mse_avg", "i16"), (24, "Data_mse_dev", "i16"),
    (25, "Data_mse_min", "i16"), (26, "Data_mse_max", "i16"),
]

SIG_NA_FIELDS = [
    (1, "Preframe_noise_count", "u32"),
    (3, "Preframe_noise_rss_avg", "i16"), (4, "Preframe_noise_rss_dev", "i16"),
    (5, "Preframe_noise_rss_min", "i16"), (6, "Preframe_noise_rss_max", "i16"),
    (7, "Preframe_noise_att_att1", "u16"),
    (8, "Preframe_noise_att_att2_avg", "i16"), (9, "Preframe_noise_att_att2_min", "i16"),
    (10, "Preframe_noise_att_att2_max", "i16"),
    (20, "Hdrerr_other_count", "u32"),
    (22, "Hdrerr_other_rss_avg", "i16"), (23, "Hdrerr_other_rss_dev", "i16"),
    (24, "Hdrerr_other_rss_min", "i16"), (25, "Hdrerr_other_rss_max", "i16"),
    (26, "Hdrerr_other_mse_avg", "i16"), (27, "Hdrerr_other_mse_dev", "i16"),
    (28, "Hdrerr_other_mse_min", "i16"), (29, "Hdrerr_other_mse_max", "i16"),
]

ACM_FIELDS = [
    (1, "radio_acm.ip_address", "ipv4"),
    (3, "radio_acm.link_addr", "mac"),
    (6, "radio_acm.mc", "hex"),
    (7, "radio_acm.profile", "enum:profile"),
    (8, "radio_acm.block_state", "enum:block_state"),
    (9, "radio_acm.connection", "enum:connection"),
    (10, "radio_acm.rmt_mse", "x10"),
    (11, "radio_acm.rx_mse", "x10"),
    (12, "radio_acm.relay_mse", "x10"),
    (13, "radio_acm.tstamp", "ts"),
]

# Enumerations: 0 = unknown, values are appended, never renumbered.
ENUMS = {
    "profile": {
        "2CPFSK_3_4": 1, "4CPFSK_3_4": 2, "4CPFSK_1_1": 3, "DPSK_3_4": 4, "pi4DQPSK_3_4": 5,
        "16DEQAM_3_4": 6, "64QAM_3_4": 7, "256QAM_3_4": 8, "256QAM_5_6": 9,
    },
    "modulation": {
        "2CPFSK": 1, "4CPFSK": 2, "DPSK": 3, "pi4DQPSK": 4, "16DEQAM": 5, "64QAM": 6, "256QAM": 7,
    },
    "block_state": {"free": 1, "stepping_up": 2, "blocking": 3},
    "connection": {"up": 1, "down": 2},
}

RF_POWER_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*(?:\(\s*([^)\s]+)\s*\))?")

log = logging.getLogger("modbus_radio")

stop_requested = False
_warned = set()


def _on_sigterm(signum, frame):  # noqa: ARG001
    global stop_requested
    stop_requested = True


def setup_logging(level):
    """One stdout handler; the launcher stores the output, so no file handler (and no logging.handlers)."""
    logging.basicConfig(level=level, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")


def warn_once(key, msg):
    """Data-shape warnings repeat every poll; log each distinct one only once per run."""
    if key not in _warned:
        _warned.add(key)
        log.warning(msg)


# --- value encoding ----------------------------------------------------------------------------

def _int(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None


def enc_u16(v):
    n = _int(v)
    return [n] if n is not None and 0 <= n < NA_U16 else [NA_U16]


def enc_i16(v):
    n = _int(v)
    return [n & 0xFFFF] if n is not None and -0x8000 < n <= 0x7FFF else [NA_I16]


def enc_u32(v):
    n = _int(v)
    if n is None or not 0 <= n < NA_U32:
        n = NA_U32
    return [n >> 16, n & 0xFFFF]


def enc_x10(v):
    try:
        return enc_i16(None if v is None else float(v) * 10)
    except (TypeError, ValueError):
        return [NA_I16]


def parse_ipv4(text):
    try:
        parts = [int(p) for p in str(text).strip().split(".")]
    except ValueError:
        return None
    if len(parts) != 4 or not all(0 <= p <= 255 for p in parts):
        return None
    return parts


def enc_ipv4(v):
    p = parse_ipv4(v)
    return [0, 0] if p is None else [(p[0] << 8) | p[1], (p[2] << 8) | p[3]]


def enc_mac(v):
    """'00:02:a9:20:4d:89' or the short link address ':20:49:69' → 3 registers (right-aligned)."""
    try:
        octets = [int(p, 16) for p in str(v).split(":") if p != ""]
    except ValueError:
        octets = []
    if not octets or len(octets) > 6 or not all(0 <= o <= 255 for o in octets):
        return [0, 0, 0]
    b = bytes(6 - len(octets)) + bytes(octets)
    return list(struct.unpack(">3H", b))


def enc_hex(v):
    try:
        n = int(str(v).strip(), 16)
    except (TypeError, ValueError):
        return [NA_U16]
    return [n] if 0 <= n < NA_U16 else [NA_U16]


def enc_ascii(text, nregs):
    b = str(text or "").encode("ascii", "replace")[: nregs * 2]
    b = b + bytes(nregs * 2 - len(b))
    return list(struct.unpack(f">{nregs}H", b))


def enc_enum(name, v):
    if v is None:
        return [0]
    key = str(v).rsplit(".", 1)[-1]
    if name == "profile":
        key = key.replace("LB_radio_acm_profiles_", "")
    code = ENUMS[name].get(key)
    if code is None:
        warn_once(f"{name}:{key}", f"unknown {name} value {v!r} → 0")
        return [0]
    return [code]


def encode(kind, v):
    if kind.startswith("enum:"):
        return enc_enum(kind[5:], v)
    return {"u16": enc_u16, "i16": enc_i16, "u32": enc_u32, "ts": enc_u32, "ipv4": enc_ipv4,
            "mac": enc_mac, "x10": enc_x10, "hex": enc_hex}[kind](v)


# --- register image ----------------------------------------------------------------------------

class Image:
    def __init__(self):
        self.regs = array.array("H", bytes(2 * 65536))

    def put(self, addr, values):
        for i, v in enumerate(values):
            self.regs[addr + i] = v & 0xFFFF

    def clear(self, start, end):
        self.regs[start:end] = array.array("H", bytes(2 * (end - start)))

    def put_fields(self, base, fields, record):
        for off, key, kind in fields:
            if key not in record:
                warn_once(f"missing:{key}", f"source column {key!r} missing → not available")
            self.put(base + off, encode(kind, record.get(key)))

    def to_bytes(self):
        a = array.array("H", self.regs)
        if sys.byteorder == "little":
            a.byteswap()
        return a.tobytes()


class State:
    """Poll results shared between the poller (writer) and the server (reader of `snapshot`)."""

    def __init__(self, args):
        self.args = args
        self.image = Image()
        self.lock = threading.Lock()
        self.snapshot = self.image.to_bytes()
        self.fatal_exit = None
        self.status = {"ok": None, "errors": 0, "seq": 0, "tstamp": 0}
        self.stats = {"ok": None, "errors": 0, "seq": 0, "tstamp": 0, "from": 0, "until": 0}
        self.sig_rows = 0
        self.acm_rows = 0
        self.acm_up = 0
        self.firmware = ""
        self.station = ""
        self.image.put(INFO_BASE, [MAGIC, MAP_VERSION])
        self._table_headers()
        self.publish()

    def _table_headers(self):
        for base, tid, src, rows, window in (
            (SIG_TABLE_BASE, SIG_TABLE_ID, self.stats, self.sig_rows, True),
            (ACM_TABLE_BASE, ACM_TABLE_ID, self.status, self.acm_rows, False),
        ):
            state = SRC_NO_DATA if src["ok"] is None else (SRC_OK if src["ok"] else SRC_FAILED)
            self.image.put(base, [tid, TABLE_STRIDE, TABLE_MAX, rows, src["seq"] & 0xFFFF, state])
            self.image.put(base + 6, enc_u32(src["tstamp"]))
            self.image.put(base + 8, enc_u32(src["from"] if window else 0))
            self.image.put(base + 10, enc_u32(src["until"] if window else 0))

    def _info(self):
        a = self.args
        health = (1 if self.status["ok"] else 0) | (2 if self.stats["ok"] else 0) \
            | (4 if self.status["ok"] or self.stats["ok"] else 0)
        self.image.put(INFO_BASE + 6, [health, min(self.status["errors"] + self.stats["errors"], 0xFFFF),
                                       a.status_interval, a.stat_interval])
        self.image.put(INFO_BASE + 10, enc_u32(a.stat_window))
        self.image.put(INFO_BASE + 20, enc_ascii(self.firmware, 16))
        self.image.put(INFO_BASE + 36, enc_ascii(self.station, 32))

    def publish(self):
        self._info()
        self._table_headers()
        snap = self.image.to_bytes()
        with self.lock:
            self.snapshot = snap

    # -- writers (poller thread only) --

    def set_device_info(self, info):
        self.firmware = info.firmware_version or ""
        self.station = info.name or ""

    def set_status(self, radio, acm):
        img = self.image
        img.clear(RADIO_BASE, RADIO_BASE + 100)
        if radio:
            r = radio[0]
            baud = r.get("radio.baudrate")
            try:
                baud = None if baud is None else float(baud) * 1000
            except ValueError:
                warn_once("baud", f"unparsable baudrate {baud!r}")
                baud = None
            rf = r.get("radio.rf_power_rms")
            m = RF_POWER_RE.match(str(rf)) if rf is not None else None
            if rf is not None and not m:
                warn_once("rf", f"unparsable RF power {rf!r}")
            img.put(RADIO_BASE, [1])
            img.put(RADIO_BASE + 1, enc_u32(baud))
            img.put(RADIO_BASE + 3, enc_mac(r.get("radio.macaddr")))
            img.put(RADIO_BASE + 6, enc_x10(m.group(1) if m else None))
            img.put(RADIO_BASE + 7, enc_u16(float(m.group(2)) * 1000 if m else None))
            img.put(RADIO_BASE + 8, enc_enum("modulation", m.group(3)) if m and m.group(3) else [0])
            img.put(RADIO_BASE + 10, enc_ascii(rf, 16))

        rows = self._table(ACM_TABLE_BASE, ACM_FIELDS, acm, "radio_acm.ip_address", "ACM")
        up = sum(1 for r in acm[:rows] if str(r.get("radio_acm.connection", "")).endswith(".up"))
        down = sum(1 for r in acm[:rows] if str(r.get("radio_acm.connection", "")).endswith(".down"))
        img.put(RADIO_BASE + 30, [rows, up, down])
        self.acm_rows, self.acm_up = rows, up

    def set_stats(self, sig, sig_na):
        img = self.image
        img.clear(SIG_NA_BASE, SIG_NA_BASE + 100)
        if sig_na:
            img.put(SIG_NA_BASE, [1])
            img.put_fields(SIG_NA_BASE, SIG_NA_FIELDS, sig_na[0])
        self.sig_rows = self._table(SIG_TABLE_BASE, SIG_FIELDS, sig, "IP_address", "signal statistics")

    def _table(self, base, fields, records, ip_key, what):
        def order(r):
            p = parse_ipv4(r.get(ip_key))
            return (0, p) if p else (1, [str(r.get(ip_key))])
        records = sorted(records, key=order)
        if len(records) > TABLE_MAX:
            warn_once(f"max:{what}", f"{len(records)} {what} rows, only {TABLE_MAX} published")
            del records[TABLE_MAX:]
        self.image.clear(base + TABLE_STRIDE, base + 10000)
        for n, rec in enumerate(records, start=1):
            rb = base + TABLE_STRIDE * n
            self.image.put(rb, [1])
            self.image.put_fields(rb, fields, rec)
        return len(records)


# --- poller thread -----------------------------------------------------------------------------

def poller(state, device):
    args = state.args
    next_status = next_stats = 0.0
    have_info = False
    while not stop_requested:
        now = time.time()
        changed = False
        try:
            if not have_info:
                try:
                    state.set_device_info(device.device_info_get_private().result)
                    have_info = True
                    changed = True
                except Ra2SessionExpiredError:
                    raise
                except Ra2MgmtError as exc:
                    warn_once("devinfo", f"device_info_get_private failed: {exc}")
            if now >= next_status:
                next_status = now + args.status_interval
                changed |= _poll(state, state.status, "status", lambda: _poll_status(state, device))
            if now >= next_stats:
                next_stats = now + args.stat_interval
                changed |= _poll(state, state.stats, "statistics", lambda: _poll_stats(state, device))
        except Ra2SessionExpiredError:
            log.error("RA2 API token expired; exiting so the next run gets a fresh one")
            state.fatal_exit = 2
            break
        if changed:
            state.publish()
        time.sleep(1)                       # 1 s tick: SIGTERM is honoured within a second


def _poll(state, src, what, fn):
    t0 = time.monotonic()
    try:
        fn()
    except Ra2SessionExpiredError:
        raise
    except Exception as exc:  # noqa: BLE001 — keep serving the last data, report once per streak
        src["errors"] += 1
        if src["ok"] is not False:
            log.warning("%s poll failed (%s): %s", what, type(exc).__name__, exc,
                        exc_info=log.isEnabledFor(logging.DEBUG))
        else:
            log.debug("%s poll failed again (%d in a row): %s", what, src["errors"], exc)
        src["ok"] = False
        return True
    src["seq"] += 1
    src["tstamp"] = int(time.time())
    log.debug("%s poll done in %.2f s (seq %d)", what, time.monotonic() - t0, src["seq"])
    if src["ok"] is not True:
        log.info(f"{what} poll ok: radio {'yes' if state.image.regs[RADIO_BASE] else 'no'}, "
            f"{state.acm_rows} ACM link(s) ({state.acm_up} up), {state.sig_rows} signal row(s)"
            + (f" (after {src['errors']} error(s))" if src["errors"] else ""))
    src["ok"] = True
    src["errors"] = 0
    return True


def _poll_status(state, device):
    res = device.helper.status_info(["radio", "radio_acm"])
    state.set_status(res.get("radio", []), res.get("radio_acm", []))


def _poll_stats(state, device):
    until = int(time.time())
    frm = until - state.args.stat_window
    res = device.helper.statistics(["stat_rlp_sig", "stat_rlp_sig_na"], tstamp_from=frm, tstamp_until=until)
    state.set_stats(res.get("stat_rlp_sig", []), res.get("stat_rlp_sig_na", []))
    state.stats["from"], state.stats["until"] = frm, until


# --- Modbus TCP server -------------------------------------------------------------------------

def _readable(addr, qty):
    end = addr + qty
    return any(s <= addr and end <= e for s, e in BLOCKS)


def handle_pdu(pdu, snapshot, dynamic):
    fc = pdu[0]
    if fc not in (3, 4):
        return bytes([fc | 0x80, 1])
    if len(pdu) != 5:
        return bytes([fc | 0x80, 3])
    addr, qty = struct.unpack(">HH", pdu[1:5])
    if not 1 <= qty <= 125:
        return bytes([fc | 0x80, 3])
    if not _readable(addr, qty):
        return bytes([fc | 0x80, 2])
    data = bytearray(snapshot[2 * addr: 2 * (addr + qty)])
    for reg, val in dynamic.items():            # uptime, time, clients: live values
        if addr <= reg < addr + qty:
            struct.pack_into(">H", data, 2 * (reg - addr), val)
    return bytes([fc, 2 * qty]) + bytes(data)


class Client:
    def __init__(self, sock, peer):
        self.sock, self.peer = sock, peer
        self.inbuf = bytearray()
        self.outbuf = bytearray()
        self.last = time.monotonic()


class Server:
    def __init__(self, state):
        self.state = state
        a = state.args
        self.sel = selectors.DefaultSelector()
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind((a.bind, a.port))
        self.lsock.listen(a.max_clients)
        self.lsock.setblocking(False)
        self.sel.register(self.lsock, selectors.EVENT_READ, None)
        self.clients = {}
        self.started = time.monotonic()
        self.requests = 0
        self.exceptions = 0
        self.rejected = 0

    def dynamic(self):
        up = int(time.monotonic() - self.started)
        now = int(time.time())
        return {INFO_BASE + 2: (up >> 16) & 0xFFFF, INFO_BASE + 3: up & 0xFFFF,
                INFO_BASE + 4: (now >> 16) & 0xFFFF, INFO_BASE + 5: now & 0xFFFF,
                INFO_BASE + 12: len(self.clients)}

    def close(self, c, why="closed"):
        log.debug("client %s:%d %s", *c.peer[:2], why)
        self.sel.unregister(c.sock)
        c.sock.close()
        self.clients.pop(c.sock, None)

    def accept(self):
        try:
            sock, peer = self.lsock.accept()
        except OSError:
            return
        if len(self.clients) >= self.state.args.max_clients:
            self.rejected += 1
            log.debug("client %s:%d rejected (max %d clients)", *peer[:2], self.state.args.max_clients)
            sock.close()
            return
        log.debug("client %s:%d connected", *peer[:2])
        sock.setblocking(False)
        c = Client(sock, peer)
        self.clients[sock] = c
        self.sel.register(sock, selectors.EVENT_READ, c)

    def serve(self, c, mask):
        if mask & selectors.EVENT_READ:
            try:
                chunk = c.sock.recv(1024)
            except (BlockingIOError, InterruptedError):
                chunk = None
            except OSError:
                self.close(c)
                return
            if chunk == b"":
                self.close(c)
                return
            if chunk:
                c.last = time.monotonic()
                c.inbuf += chunk
                if not self.frames(c):
                    self.close(c)
                    return
        self.flush(c)

    def frames(self, c):
        with self.state.lock:
            snap = self.state.snapshot
        uid_filter = self.state.args.unit_id
        while len(c.inbuf) >= 7:
            tid, pid, length, uid = struct.unpack(">HHHB", c.inbuf[:7])
            if pid != 0 or not 2 <= length <= 254 or len(c.inbuf) > 4096:
                log.debug("client %s:%d sent an invalid MBAP header", *c.peer[:2])
                return False
            if len(c.inbuf) < 6 + length:
                break
            pdu = bytes(c.inbuf[7: 6 + length])
            del c.inbuf[: 6 + length]
            if uid_filter is not None and uid != uid_filter:
                continue
            resp = handle_pdu(pdu, snap, self.dynamic())
            self.requests += 1
            if resp[0] & 0x80:
                self.exceptions += 1
            if log.isEnabledFor(logging.DEBUG):
                req = (f"FC{pdu[0]:02d} addr {int.from_bytes(pdu[1:3], 'big')} qty {int.from_bytes(pdu[3:5], 'big')}"
                       if len(pdu) >= 5 else f"FC{pdu[0]:02d} {pdu[1:].hex(' ')}")
                log.debug("client %s:%d uid %d %s → %s", *c.peer[:2], uid, req,
                          f"exception {resp[1]}" if resp[0] & 0x80 else f"{resp[1] // 2} register(s)")
            c.outbuf += struct.pack(">HHHB", tid, 0, len(resp) + 1, uid) + resp
        return True

    def flush(self, c):
        if c.outbuf:
            try:
                n = c.sock.send(c.outbuf)
                del c.outbuf[:n]
            except (BlockingIOError, InterruptedError):
                pass
            except OSError:
                self.close(c)
                return
        events = selectors.EVENT_READ | (selectors.EVENT_WRITE if c.outbuf else 0)
        self.sel.modify(c.sock, events, c)

    def run(self):
        last_summary = time.monotonic()
        while not stop_requested and self.state.fatal_exit is None:
            for key, mask in self.sel.select(timeout=1.0):
                if key.data is None:
                    self.accept()
                elif key.fileobj in self.clients:
                    self.serve(key.data, mask)
            now = time.monotonic()
            for c in list(self.clients.values()):
                if now - c.last > 60:
                    self.close(c, "idle timeout")
            if now - last_summary >= 600:
                last_summary = now
                self.summary()
        for c in list(self.clients.values()):
            self.close(c)
        self.sel.close()
        self.lsock.close()

    def summary(self):
        s = self.state
        log.info(f"summary: clients {len(self.clients)}, requests {self.requests} "
            f"({self.exceptions} exceptions), rejected {self.rejected}; "
            f"status {'ok' if s.status['ok'] else 'FAIL'}, statistics {'ok' if s.stats['ok'] else 'FAIL'}, "
            f"{s.acm_rows} ACM link(s), {s.sig_rows} signal row(s)")


def main() -> int:
    payload = json.load(sys.stdin)
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    signal.signal(signal.SIGTERM, _on_sigterm)

    parser = argparse.ArgumentParser(description="Modbus TCP mirror of radio statistics and status")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--status-interval", type=int, default=60)
    parser.add_argument("--stat-interval", type=int, default=60)
    parser.add_argument("--stat-window", type=int, default=900)
    parser.add_argument("--max-clients", type=int, default=8)
    parser.add_argument("--unit-id", type=int, default=None)
    parser.add_argument("--api-host", default="localhost")
    parser.add_argument("--log-level", default="INFO", type=str.upper,
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args()
    setup_logging(args.log_level)
    for name in ("status_interval", "stat_interval", "stat_window", "max_clients"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be >= 1")

    token = payload.get("ra2_api_token")
    if not token:
        log.error("no ra2_api_token in the payload (firmware older than 2.3.10.0?)")
        return 1

    state = State(args)
    try:
        server = Server(state)
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", args.bind, args.port, exc)
        return 1
    log.info(f"Modbus TCP server on {args.bind}:{args.port} (map v{MAP_VERSION}), "
        f"status every {args.status_interval} s, statistics every {args.stat_interval} s "
        f"over {args.stat_window} s, max {args.max_clients} clients")

    device = Device(host=args.api_host, token=token, timeout=20, retries=1)
    with device:
        t = threading.Thread(target=poller, args=(state, device), name="poller", daemon=True)
        t.start()
        server.run()
        t.join(timeout=5)
    server.summary()
    log.info("stopped" if stop_requested else "finished")
    return state.fatal_exit or 0


if __name__ == "__main__":
    sys.exit(main())

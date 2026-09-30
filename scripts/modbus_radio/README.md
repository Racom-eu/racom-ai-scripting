# modbus_radio — Modbus TCP mirror of radio statistics and radio status (RA2)

| file | runs on | purpose |
|------|---------|---------|
| `modbus_radio.py` | the RA2 (Scripting instance) | Modbus TCP server; polls the local unit over the RA2 API |
| `modbus_read.py` | laptop / any PC, Python ≥ 3.9, stdlib only | reads and decodes the whole map, raw register dump |
| `PROPOSAL.md` | — | design record: findings, architecture, risks, test plan |

## Deployed

RA2 `192.168.169.169` (fw 2.3.10.0): script `modbus_radio.py`, instance `mb1502`, arguments
`--port 1502`, role admin, manual start trigger. It does **not** start by itself after a reboot.

```bash
S=skills/scripting/scripts/scripting
$S list                                     # state of the instance
$S logs modbus_radio.py mb1502 [--follow]   # output of the last run
$S stop  modbus_radio.py mb1502
$S start modbus_radio.py mb1502
$S deploy scripts/modbus_radio/modbus_radio.py --instance mb1502 --set arguments='--port 1502'   # after edits, then stop+start
```

## Test client

```bash
python3 scripts/modbus_radio/modbus_read.py                    # host from RA2_HOST / .env.ra2, port 1502
python3 scripts/modbus_radio/modbus_read.py 192.168.169.169 -p 1502
python3 scripts/modbus_radio/modbus_read.py --watch 10         # refresh every 10 s
python3 scripts/modbus_radio/modbus_read.py --json             # decoded map as JSON
python3 scripts/modbus_radio/modbus_read.py --dump             # every used register as "address: value" (--signed, --hex, --fc 3)
python3 scripts/modbus_radio/modbus_read.py --raw 10050 27     # "address: value" for one range
```

## Script arguments

| argument | default | meaning |
|----------|---------|---------|
| `--port` | *required* | TCP port |
| `--bind` | `0.0.0.0` | listen address |
| `--status-interval` | 60 s | refresh of `radio` / `radio_acm` |
| `--stat-interval` | 60 s | refresh of the signal statistics |
| `--stat-window` | 900 s | statistics are aggregated over `now − window … now` |
| `--max-clients` | 8 | concurrent clients (the next one is closed on accept); idle clients are closed after 60 s |
| `--unit-id` | any | answer only this Unit ID (default: any Unit ID, echoed back) |
| `--api-host` | `localhost` | RA2 API host, for testing only |
| `--log-level` | `INFO` | `DEBUG` adds every client connect/close and every request (FC, address, quantity, result) — mind the 20 KiB/s output limit |

## Register map (version 1)

**Conventions**
- 0-based PDU addresses (register 10050 = "3x10051 / 4x10051" in 1-based tools).
  **FC 03 and FC 04 return the same map.** Writes → exception 01. An address outside the blocks
  below → exception 02. Quantity outside 1…125 → exception 03.
- 16-bit big-endian registers. **32-bit values: high word first.** `int16` = two's complement.
- **Not available:** `int16` `0x8000` (−32768) · `uint16` `0xFFFF` · `uint32` `0xFFFFFFFF`. Reserved registers read 0.
- IPv4 = 2 registers (10.10.10.10 → `0A0A 0A0A`). MAC / link address = 3 registers, right-aligned
  (link `:20:49:69` → `0000 0020 4969`). ASCII = 2 characters per register, NUL-padded.

| range | block |
|-------|-------|
| 0 – 99 | Info |
| 1000 – 1099 | Radio status (Diagnostics → Information → Radio) |
| 2000 – 2099 | Radio signal non-addressable statistics |
| 10000 – 19999 | table 1: Radio signal statistics |
| 20000 – 29999 | table 2: Radio ACM / neighbour links |
| 3000 – 9999, 30000 – 59999 | reserved for future blocks and tables |

### Tables

Header at `base + 0…49`. **Record n (1…199) at `base + 50·n`**, 50 registers, so one 100-register
read returns two records. Records are sorted by radio IP. Identify a neighbour by the IP in the
record, not by n. Use the sequence counter to detect a refresh between two reads.

| off | type | header field |
|-----|------|--------------|
| 0 | uint16 | table id (1 signal, 2 ACM) |
| 1 | uint16 | record stride (50) |
| 2 | uint16 | max records (199) |
| 3 | uint16 | record count |
| 4 | uint16 | sequence, +1 per successful refresh |
| 5 | uint16 | source state: 0 ok · 1 no data yet · 2 last refresh failed (old data kept) |
| 6–7 | uint32 | last successful refresh, unix |
| 8–11 | 2× uint32 | statistics window from / until, unix (0 in table 2) |

### Info (0)

| addr | type | field |
|------|------|-------|
| 0 | uint16 | magic `0x5241` ("RA") |
| 1 | uint16 | map version (1) |
| 2–3 | uint32 | script uptime [s] |
| 4–5 | uint32 | device time, unix |
| 6 | uint16 | health: b0 status poll ok · b1 statistics poll ok · b2 API reachable |
| 7 | uint16 | consecutive poll errors |
| 8 / 9 | uint16 | status / statistics interval [s] |
| 10–11 | uint32 | statistics window [s] |
| 12 | uint16 | connected clients |
| 20–35 | ascii[32] | firmware version |
| 36–67 | ascii[64] | station name |

### Radio status (1000)

| addr | type | field |
|------|------|-------|
| 1000 | uint16 | valid (1) |
| 1001–1002 | uint32 | baudrate [Bd] (10.417 kBaud → 10417) |
| 1003–1005 | mac | radio MAC address |
| 1006 | int16 ×10 | RF power RMS [dBm] (200 = 20.0 dBm) |
| 1007 | uint16 | RF power RMS [mW] |
| 1008 | enum M | modulation of the RF power figure |
| 1010–1025 | ascii[32] | RF power text as in the UI (`20 / 0.1 (2CPFSK)`) |
| 1030 / 1031 / 1032 | uint16 | ACM links total / up / down |

### Radio signal non-addressable statistics (2000)

| addr | type | field |
|------|------|-------|
| 2000 | uint16 | valid (1) |
| 2001–2002 | uint32 | Pre-frame · count |
| 2003 / 2004 / 2005 / 2006 | int16 | Pre-frame · RSS avg / dev / min / max [dBm] |
| 2007 | uint16 | Pre-frame · Att1 [%] |
| 2008 / 2009 / 2010 | int16 | Pre-frame · Att2 avg / min / max [dB] |
| 2020–2021 | uint32 | Others · count |
| 2022 … 2025 | int16 | Others · RSS avg / dev / min / max [dBm] |
| 2026 … 2029 | int16 | Others · Phy header MSE avg / dev / min / max [dB] |

Window and refresh time: see the table 1 header (the same poll fills both).

### Table 1 — Radio signal statistics (10000 + 50·n)

| off | type | field (UI column) |
|-----|------|-------------------|
| 0 | uint16 | valid (1) |
| 1–2 | ipv4 | Radio IP address |
| 3–5 | mac | Link address |
| 6–7 | uint32 | Header count |
| 8 / 9 / 10 / 11 | int16 | RSS avg / dev / min / max [dBm] |
| 12 / 13 / 14 / 15 | int16 | Phy header MSE avg / dev / min / max [dB] |
| 16 | int16 | Freq offset [Hz] |
| 17 | uint16 | Att1 [%] |
| 18 / 19 / 20 | int16 | Att2 avg / min / max [dB] |
| 21–22 | uint32 | Data count |
| 23 / 24 / 25 / 26 | int16 | Data MSE avg / dev / min / max [dB] |

### Table 2 — Radio ACM / neighbour links (20000 + 50·n)

| off | type | field (UI column) |
|-----|------|-------------------|
| 0 | uint16 | valid (1) |
| 1–2 | ipv4 | Target · Radio IP address |
| 3–5 | mac | Target · Link address |
| 6 | uint16 | Control · MC (UI shows hex: `10` → 16) |
| 7 | enum P | Control · Last used ACM profile |
| 8 | enum S | Control · State |
| 9 | enum C | Control · Connection |
| 10 / 11 / 12 | int16 ×10 | Feedback MSE / RX MSE / BDP Relay MSE [dB] |
| 13–14 | uint32 | Last measurement, unix |

### Enumerations (0 = unknown / not available)

| enum | values |
|------|--------|
| P profile | 1 2CPFSK 3/4 · 2 4CPFSK 3/4 · 3 4CPFSK 1/1 · 4 DPSK 3/4 · 5 π/4DQPSK 3/4 · 6 16DEQAM 3/4 · 7 64QAM 3/4 · 8 256QAM 3/4 · 9 256QAM 5/6 |
| M modulation | 1 2CPFSK · 2 4CPFSK · 3 DPSK · 4 π/4DQPSK · 5 16DEQAM · 6 64QAM · 7 256QAM |
| S ACM state | 1 free · 2 stepping up · 3 blocking |
| C connection | 1 up · 2 down |

## Extending

- **New field:** use the next reserved offset. Add one line to the field table in
  `modbus_radio.py` (`SIG_FIELDS`, `SIG_NA_FIELDS`, `ACM_FIELDS`) and in `modbus_read.py`, then bump `MAP_VERSION`.
- **New statistics type** (e.g. `stat_rlp_proto`): a new table at the next free 10000 base, with the same header and record rules.
- **New status type** (e.g. `antenna_state`): a new scalar block at the next free 1000.
- Fields are mapped by CSV column and status key names, never by position. If the firmware drops
  a column, the field reads "not available" and one log line is written.

## Notes

- Statistics are aggregated in device buckets of a few minutes. A window below ~5 min can return
  an empty table (record count 0, state ok).
- Modbus has no authentication. The L3 firewall is off on this unit, so 1502 is open on every
  interface. Restrict it with `--bind` or with a firewall rule when needed.

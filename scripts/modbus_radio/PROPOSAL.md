# Proposal — `modbus_radio.py`: Modbus TCP mirror of radio statistics and radio status (RA2)

Status: **approved and implemented** (see README.md) · target: RA2 `192.168.169.169`, firmware 2.3.10.0 · 2026-09-25

## 1. Goal

A Scripting script, running inside the RA2, that:

1. starts a Modbus TCP server on a port passed as an argument (`--port 1502`),
2. reads the local unit over the RA2 API (token mode, `localhost`),
3. mirrors two web-UI pages into registers:
   - **Diagnostics → Statistics → Radio signal statistics** (`stat_rlp_sig`, one row per neighbour)
     plus its companion **Radio signal non-addressable statistics** (`stat_rlp_sig_na`),
   - **Diagnostics → Information → Radio** (`radio`: baudrate, MAC, RF power, and `radio_acm`: the
     per-neighbour link / ACM table).

## 2. Findings (measured on the target unit)

| topic | finding | consequence |
|-------|---------|-------------|
| firmware | 2.3.10.0 → `rr_ra2_mgmt` + `ra2_api_token` available in the script | RA2 API in token mode, no NETCONF needed (read-only task) |
| Modbus library | none on the device (no `pymodbus`); stdlib has `socket`, `selectors`, `struct`, `threading` | small built-in Modbus TCP server (FC 03/04 only, ~150 lines) |
| Scripting | `RR_Sdk_Enable = 0` (`script_overview_get` → `rpc_method_not_found`) | must be switched **On** before deploying (state change, confirmation needed) |
| firewall | L3 firewall off, `INPUT` policy ACCEPT; the service chains only touch ports 22/80/443/8889 | 1502 reachable from LAN and radio without a rule; if the L3 firewall is enabled later, a user INPUT rule for tcp/1502 is needed |
| open files | soft limit 15 | raise `RLIMIT_NOFILE` soft → hard (100), cap clients (`--max-clients`, default 8) |
| CPU | soft limit 20 s | raise `RLIMIT_CPU` (long-running server) |
| signal statistics | `statistics_data_get(types, tstamp_from)` aggregates over the window; data are integers; with a window shorter than the device's bucket (≤ 5 min here) the table is empty | configurable window (default 900 s); an empty table is a valid state, not an error |
| column layout | CSV template (`statistics_csv_get`) names the columns (`Rss_avg`, `Data_mse_max`, …); the data array is in another order | decode **by column name** via `device.helper.statistics()`, never by index |
| radio status | `radio` = `["10.417", "00:02:a9:20:4d:89", "20 / 0.1 (2CPFSK)"]` — display strings | parse into numbers; also publish the raw RF-power text as ASCII |
| ACM table | `radio_acm` row: link address, IP, MC (hex), profile, block state, connection, 3× MSE, last measurement; MSE / tstamp `null` until ACM measures | enums + "not available" sentinel |
| "neighbours" status type | `neighbors` is the Linux ARP table, not radio data | not used; the neighbour link info on the Radio page is `radio_acm` |
| link address | reported short (`:20:49:69`) = last 3 bytes of the neighbour's radio MAC | stored as 3 bytes |

Values seen on the unit now: one neighbour `10.10.10.10`, RSS −24 dBm, data MSE −27 dB, ACM profile 2CPFSK 3/4, connection up, baudrate 10.417 kBd, RF 20 dBm / 0.1 W.

## 3. Architecture

```
            ┌──────────── RA2 (Scripting instance "modbus_radio") ────────────┐
 SCADA ───► │ main thread: selectors loop, Modbus TCP :1502 (FC03 = FC04)     │
            │         ▲ reads the current immutable snapshot (bytes)          │
            │ poller thread: every --status-interval / --stat-interval        │
            │   helper.status_info(["radio","radio_acm"])                     │
            │   helper.statistics(["stat_rlp_sig","stat_rlp_sig_na"], window) │
            │   → builds a new register image → swaps the snapshot reference  │
            └──────────────────────── RA2 API https://localhost ──────────────┘
```

- **Two threads.** A slow or hung API call (library timeout up to 30 s × retries) must not stall
  Modbus. Only the poller touches `Device` (it is not thread-safe). The image is replaced
  whole, so every Modbus request sees one consistent snapshot.
- **Read-only server.** FC 03 (holding) and FC 04 (input) return the same map. Write functions →
  exception 01. An address outside a defined block → exception 02. Quantity outside 1…125 → exception 03.
  Any Unit ID is accepted and echoed (optional `--unit-id N` to restrict).
- **Stop.** A SIGTERM handler sets a flag. The select loop (1 s timeout) and the poller (1 s sleep
  steps) both exit, and the sockets close → exit 0. Token expiry (`Ra2SessionExpiredError`) → exit 2,
  as in the templates.
- **Output.** A start line, one line per poll **error**, and a summary line every 10 minutes
  (clients, requests, last poll). No per-request logging, because of the output rate limit.
- **Clients.** Non-blocking sockets, at most `--max-clients`, 60 s idle timeout, partial
  frames buffered, malformed MBAP header → connection closed.

### Arguments

| argument | default | meaning |
|----------|---------|---------|
| `--port` | *required* | TCP port (1502) |
| `--bind` | `0.0.0.0` | listen address |
| `--status-interval` | 60 s | refresh of `radio` / `radio_acm` |
| `--stat-interval` | 60 s | refresh of signal statistics |
| `--stat-window` | 900 s | statistics window (`tstamp_from = now − window`), like the period picker in the UI |
| `--max-clients` | 8 | concurrent TCP clients |
| `--unit-id` | any | answer only this Unit ID |

## 4. Register map — conventions

- Addresses are **0-based PDU addresses** (register 10050 = "4x10051" in 1-based tools).
- 16-bit registers, big-endian. **32-bit values: high word first** (ABCD).
- `int16` two's complement. **"not available" sentinels:** `int16` → `0x8000` (−32768),
  `uint16` → `0xFFFF`, `uint32` → `0xFFFFFFFF`. Unused and reserved registers read `0`.
- IPv4 = `uint32` (10.10.10.10 → `0x0A0A`, `0x0A0A`). MAC / link address = 3 registers, 6 bytes
  (link address `:20:49:69` → `0x0000 0x0020 0x4969`).
- ASCII strings: 2 characters per register, high byte first, NUL-padded.
- Scaling is written in the tables (`×10` = value / 10). Statistics are integers on the device,
  so they are stored as-is (scale 1).

### Address space — designed for extension

| range | block | kind |
|-------|-------|------|
| 0 – 99 | **Info** (server, device, health) | scalar |
| 1000 – 1099 | **Radio status** (`radio`) + ACM summary | scalar |
| 2000 – 2099 | **Radio signal non-addressable statistics** (`stat_rlp_sig_na`) | scalar |
| 3000 – 9999 | *reserved for future scalar blocks (1000 each)* | |
| 10000 – 19999 | **Radio signal statistics** (`stat_rlp_sig`) | table |
| 20000 – 29999 | **Radio ACM / neighbour links** (`radio_acm`) | table |
| 30000 – 59999 | *reserved for 3 more tables (e.g. radio protocol / interface statistics)* | |

**Table rule** (same for every table): table base `B` = 10000·k.
Header at `B + 0 … B + 49`, **record n (n = 1…199) at `B + 50·n`**, 50 registers each.
One 100-register read returns two records. Each record uses fewer than 30 registers, so
about 20 stay free for new fields without moving anything. Records are **sorted by radio IP
address** and packed from n = 1. A client must identify a neighbour by the IP in the record,
not by n: when a neighbour disappears, the records after it shift.

**Table header** (`B + offset`):

| off | type | field |
|-----|------|-------|
| 0 | uint16 | table id (1 = signal stats, 2 = ACM) |
| 1 | uint16 | record stride (50) |
| 2 | uint16 | max records (199) |
| 3 | uint16 | record count |
| 4 | uint16 | update sequence (+1 on every refresh; read it before and after a multi-request scan) |
| 5 | uint16 | source state: 0 ok, 1 no data yet, 2 last refresh failed (data kept) |
| 6–7 | uint32 | last successful refresh (unix, device time) |
| 8–9 | uint32 | statistics window from (unix; 0 for status tables) |
| 10–11 | uint32 | statistics window until (unix; 0 for status tables) |
| 12–49 | — | reserved |

## 5. Block 0 — Info (0 – 99)

| addr | type | field |
|------|------|-------|
| 0 | uint16 | magic `0x5241` ("RA") |
| 1 | uint16 | register-map version (1) |
| 2–3 | uint32 | script uptime [s] |
| 4–5 | uint32 | device time now [unix] |
| 6 | uint16 | health bits: b0 status poll ok, b1 statistics poll ok, b2 API reachable |
| 7 | uint16 | consecutive poll errors |
| 8 | uint16 | status interval [s] |
| 9 | uint16 | statistics interval [s] |
| 10–11 | uint32 | statistics window [s] |
| 12 | uint16 | connected Modbus clients |
| 13–19 | — | reserved |
| 20–35 | ascii[32] | firmware version (`2.3.10.0`) |
| 36–67 | ascii[64] | station name |
| 68–99 | — | reserved |

## 6. Block 1000 — Radio status (Diagnostics → Information → Radio)

| addr | type | scale / unit | field | source |
|------|------|--------------|-------|--------|
| 1000 | uint16 | | valid (1 = data present) | |
| 1001–1002 | uint32 | Bd | baudrate (10.417 kBd → 10417) | `radio.baudrate` |
| 1003–1005 | mac | | radio MAC address | `radio.macaddr` |
| 1006 | int16 | ×10 dBm | RF power RMS (20 → 200) | `radio.rf_power_rms` |
| 1007 | uint16 | mW | RF power RMS (0.1 W → 100) | 〃 |
| 1008 | uint16 | enum M | modulation of the RF power figure | 〃 |
| 1009 | — | | reserved | |
| 1010–1025 | ascii[32] | | RF power text as shown in the UI (`20 / 0.1 (2CPFSK)`) | 〃 |
| 1026–1029 | — | | reserved | |
| 1030 | uint16 | | ACM links total | count of `radio_acm` rows |
| 1031 | uint16 | | ACM links up | `connection = up` |
| 1032 | uint16 | | ACM links down | `connection = down` |
| 1033–1099 | — | | reserved | |

## 7. Block 2000 — Radio signal non-addressable statistics (`stat_rlp_sig_na`)

| addr | type | unit | field (UI column) | CSV column |
|------|------|------|-------------------|------------|
| 2000 | uint16 | | valid | |
| 2001–2002 | uint32 | | Pre-frame · Count | `Preframe_noise_count` |
| 2003 | int16 | dBm | Pre-frame · RSS avg | `Preframe_noise_rss_avg` |
| 2004 | int16 | dB | Pre-frame · RSS dev | `…_rss_dev` |
| 2005 | int16 | dBm | Pre-frame · RSS min | `…_rss_min` |
| 2006 | int16 | dBm | Pre-frame · RSS max | `…_rss_max` |
| 2007 | uint16 | % | Pre-frame · Att1 | `Preframe_noise_att_att1` |
| 2008–2010 | int16 | dB | Pre-frame · Att2 avg / min / max | `…_att2_avg/min/max` |
| 2011–2019 | — | | reserved | |
| 2020–2021 | uint32 | | Others · Count | `Hdrerr_other_count` |
| 2022–2025 | int16 | dBm | Others · RSS avg / dev / min / max | `Hdrerr_other_rss_*` |
| 2026–2029 | int16 | dB | Others · Phy header MSE avg / dev / min / max | `Hdrerr_other_mse_*` |
| 2030–2099 | — | | reserved | |

Header-like fields (window, sequence, refresh time) for this block are the same as in table 1's header (same poll).

## 8. Table 1 (10000) — Radio signal statistics (`stat_rlp_sig`)

Record n at `10000 + 50·n`, offsets follow the UI column order:

| off | type | unit | UI column | CSV column |
|-----|------|------|-----------|------------|
| 0 | uint16 | | valid (1) | |
| 1–2 | ipv4 | | Radio IP address | `IP_address` |
| 3–5 | mac | | Link address | `Link_address` |
| 6–7 | uint32 | | Header count | `Frame_count` |
| 8 | int16 | dBm | RSS avg | `Rss_avg` |
| 9 | int16 | dB | RSS dev | `Rss_dev` |
| 10 | int16 | dBm | RSS min | `Rss_min` |
| 11 | int16 | dBm | RSS max | `Rss_max` |
| 12–15 | int16 | dB | Phy header MSE avg / dev / min / max | `Subhdr_mse_*` |
| 16 | int16 | Hz | Freq offset | `Freq_offset` |
| 17 | uint16 | % | Att1 | `Att_att1` |
| 18–20 | int16 | dB | Att2 avg / min / max | `Att_att2_*` |
| 21–22 | uint32 | | Data count | `Data_count` |
| 23–26 | int16 | dB | Data MSE avg / dev / min / max | `Data_mse_*` |
| 27–49 | — | | reserved | |

Example, neighbour 1 now: `10050`=1, `10051–52`=0x0A0A 0x0A0A, `10058`=−24, `10073`=−27.

## 9. Table 2 (20000) — Radio ACM / neighbour links (`radio_acm`)

Record n at `20000 + 50·n`:

| off | type | scale | UI column | status key |
|-----|------|-------|-----------|------------|
| 0 | uint16 | | valid (1) | |
| 1–2 | ipv4 | | Target · Radio IP address | `radio_acm.ip_address` |
| 3–5 | mac | | Target · Link address | `radio_acm.link_addr` |
| 6 | uint16 | | Control · MC (hex `01` → 1) | `radio_acm.mc` |
| 7 | uint16 | enum P | Control · Last used ACM profile | `radio_acm.profile` |
| 8 | uint16 | enum S | Control · State | `radio_acm.block_state` |
| 9 | uint16 | enum C | Control · Connection | `radio_acm.connection` |
| 10 | int16 | ×10 dB | Measurements · Feedback MSE | `radio_acm.rmt_mse` |
| 11 | int16 | ×10 dB | Measurements · RX MSE | `radio_acm.rx_mse` |
| 12 | int16 | ×10 dB | Measurements · BDP Relay MSE | `radio_acm.relay_mse` |
| 13–14 | uint32 | unix | Measurements · Last measurement | `radio_acm.tstamp` |
| 15–49 | — | | reserved | |

MSE uses ×10 because the ACM values may be fractional; they are `null` here, so this is not yet verified on the device.

### Enumerations (0 = unknown / not available, always)

| enum | values |
|------|--------|
| **P** ACM profile | 1 2CPFSK 3/4 · 2 4CPFSK 3/4 · 3 4CPFSK 1/1 · 4 DPSK 3/4 · 5 π/4DQPSK 3/4 · 6 16DEQAM 3/4 · 7 64QAM 3/4 · 8 256QAM 3/4 · 9 256QAM 5/6 |
| **M** modulation | 1 2CPFSK · 2 4CPFSK · 3 DPSK · 4 π/4DQPSK · 5 16DEQAM · 6 64QAM · 7 256QAM |
| **S** ACM state | 1 free · 2 stepping up · 3 blocking |
| **C** connection | 1 up · 2 down |

The enums come from the translation keys (`radio_acm.profile_val.LB_radio_acm_profiles_*`). A key the
script does not know maps to 0 and is logged once. New values are appended and never renumbered.

## 10. How to extend

- **New field** in an existing record/block: take the next reserved offset, bump the map version
  (register 1), and add one line in the script's field table (`(offset, CSV column, type, scale)`).
- **New statistics type** (e.g. `stat_rlp_proto`, `stat_rlp_ifc`): new table at the next free
  10000 base, same header/record rule, add it to the poller's type list.
- **New status type** (e.g. `antenna_state`, `system_basic`): new scalar block at the next free 1000.
- Field mapping is by CSV column / status key names, so a firmware that reorders the data
  keeps working. A missing column → sentinel and one log line.

## 11. Risks / open points

| # | point | handling |
|---|-------|----------|
| 1 | AppArmor might deny `bind/listen` for scripts | first test step; if denied, the design cannot run in-device (fallback: laptop/gateway-side mirror) |
| 2 | RF-power / baudrate are display strings | parsed with a regex; raw text kept at 1010; parse failure → sentinel + log |
| 3 | ACM MSE / tstamp format unverified (`null` now) | accept int/float/str; confirm once ACM measures |
| 4 | stats window < device bucket → empty table | documented; header `record count = 0`, state ok |
| 5 | > 199 neighbours | extra ones dropped, logged once (header shows 199) |
| 6 | Modbus has no authentication | bind to a LAN address via `--bind` and/or enable L3 firewall with a rule for trusted sources |

## 12. Deliverables

- `scripts/modbus_radio/modbus_radio.py` — the device script (from the `continuous` template).
- `scripts/modbus_radio/modbus_read.py` — local, stdlib-only Modbus client for the laptop: dumps and
  decodes all blocks, used for testing and as a reference for SCADA integrators.
- `scripts/modbus_radio/README.md` — the register map (sections 4–9) as the user-facing document.

## 13. Test plan (after your confirmation)

Device state changes are marked ⚠.

1. `scripting check modbus_radio.py` (contract, imports vs 2.3.10.0).
2. ⚠ `scripting enable --on`: set `RR_Sdk_Enable` Off → On.
3. ⚠ `scripting deploy modbus_radio.py --instance mb1502 --set arguments='--port 1502'`
   (role admin, manual trigger, `--dry-run` shown first).
4. ⚠ `scripting start modbus_radio mb1502`, then `scripting logs --follow`: server start, first polls.
5. From the laptop: `modbus_read.py 192.168.169.169 1502`, then compare with `ra2 status radio radio_acm`
   and `ra2 helper statistics stat_rlp_sig …` (same window).
6. Negative tests: write FC06 → exc 01, address 5000 → exc 02, quantity 126 → exc 03, 10 parallel
   clients (> max 8), a half frame and then disconnect, idle timeout.
7. ⚠ `scripting stop`: execution must end `finished` with exit 0 (not `killed`).
8. Leave deployed or ⚠ `scripting undeploy modbus_radio` (and optionally Scripting back Off), as you decide.

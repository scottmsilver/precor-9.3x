# Esp32Tap Rev C interconnect and module selection

Status: `PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF`; suitable only for
conservative schematic/layout and verification-package work. It is not
order-ready and is not released for deployment, production, purchase, or
`TURNKEY_QUOTED`.

Evidence was retrieved 2026-07-24T15:04:11Z. Manufacturer product pages and
datasheets establish identity and published ratings. Official LCSC pages
establish exact catalog identity, packaging, and public stock at retrieval
time. Public catalog stock is **not** proof that JLCPCB will place a part. Every
selected SMT row remains `PROVISIONAL_REQUIRES_LIVE_BOM_CPL_PROOF`; Task 10
must prove each row in the live BOM/CPL workflow or force reselection and
regeneration. No cart, order, or account mutation was performed.

## Selection

| Interface | Board header | LCSC | Housing | LCSC | Reel terminal | LCSC |
|---|---|---|---|---|---|---|
| Console | Molex `430450809`, Micro-Fit 3.0, 8-position, right-angle SMT | `C240838` | `430250800`, 8-position | `C127351` | `430300001`, 20/22/24 AWG | `C259786` |
| Motor | Molex `430451010`, Micro-Fit 3.0, 10-position, right-angle SMT | `C563827` | `430251000`, 10-position | `C259745` | `430300001`, 20/22/24 AWG | `C259786` |

The 8-position housing cannot mate with the 10-position header and the
10-position housing cannot mate with the 8-position header. Motor positions 9
and 10 are unpopulated and require individual cavity plugs in the supplier
drawing. Labels and color are secondary defenses.

Micro-Fit is selected over the smaller candidates because the connector family
is rated 600 V and -40 to +105 °C; the selected
Alpha Wire 3051 conductors are rated 300 V and -40 to +105 °C. The 8.5 A
maximum contact class is not used as the qualified value. Molex PS-43045
publishes 22 AWG wire-to-board values of 4.5 A at 6 circuits and 4.0 A at 12
circuits, each on a 30 °C maximum temperature-rise basis. It does not publish
an 8-/10-circuit +85 °C row and explicitly requires further application
derating.

The committed validator therefore uses the worse 12-circuit 4.0 A value for
both selected circuit counts and labels the following calculation a
`CONSERVATIVE_ENGINEERING_DERIVATION`, not a manufacturer rating. At +85 °C,
the 105 °C connector limit allows 20 °C rise. I²R scaling plus an additional
0.75 safety factor gives
`4.0 * sqrt(20 / 30) * 0.75 = 2.449 A/contact`. At the required 2.0 A, the
expected rise is `30 * (2 / 4)² = 7.5 °C`, leaving 12.5 °C to the connector
limit. The validator recomputes each result and
assigns the full 2.0 A load to each remaining new header/terminal/22 AWG wire
path in turn; it never credits equal sharing. Candidate geometry is explicitly
modeled, not a completed layout. The DuraClik and TE Micro MATE-N-LOK rows
remain viable comparison families pending exact live placement and final
derating proof. The direct-SMT Molex `855437001` / `C588562` RJ45 is retained
only as the required size baseline and is rejected for the replacement
interface.

## Harness definition and RJ45 exception

Both CSV drawings map RJ45 pins 1 through 8 one-to-one. The selected board
system uses exact Alpha Wire `3051 RD005`, `WH005`, `BL005`, `GR005`,
`OR005`, `YL005`, `BK005`, and `BR005` 22 AWG conductors, Molex reel terminal
`430300001`, and the exact 8- or 10-position housing above. Power and ground
colors and labels are distinct; every conductor receives an end-to-end
continuity/resistance test with a 100 mΩ maximum. The finished assembly must
include HellermannTyton `151-00745` (`PC5.0-PA66-BK`) strain relief and a TE Connectivity
`1932219-1` non-magnetic female 8P8C through-hole PCB jack. The carrier PCB,
enclosure, and finished pigtail are not yet exact or orderable: the factory
assembly remains `PENDING_FIRM_QUOTE` and is a release blocker.
The exact factory assembly number, production drawing, tooling/NRE, test
coverage, and firm quote remain open; owner crimping or soldering is forbidden.

The legacy RJ45 boundary is an explicit predecessor-interface exception.
TE publishes 1.5 A/contact and -40 to +85 °C for `1932219-1`. Normal modeled
operation retains the PiZeroHat topology: +8 V uses RJ45 pins 2 and 8 and
ground uses pins 1 and 7. The unequal case is 1.35 A / 0.65 A for each power
pair and each ground pair, totaling 2.0 A without exceeding 1.5 A on either
contact. A single-open RJ45 contact carrying 2.0 A is
`UNSUPPORTED_OPEN_PHYSICAL_GATE`; it is not presented as rated or released.
Installed thermal/drop testing or a measured-envelope redesign must close this
before deployment or turnkey status.

Console/Motor reversal at the ordinary RJ45 ends uses a modeled Task 6
implementation concept: clip-on keyed collars fitted to distinct enclosure
apertures. Both collar bodies are 15.6 x 13.6 mm and fit 16.0 x 14.0 mm
apertures. A 3.0 x 2.0 mm rib fits a 3.4 x 2.2 mm slot. Console's key is at
x=-5.0 mm and Motor's at x=+5.0 mm, giving 10.0 mm separation and a modeled
6.6 mm wrong-mating collision margin. Modeled harness lengths are 180 mm
Console and 240 mm Motor, each with at least 25 mm service slack. Task 6 must
implement these dimensions in CAD. Delivered-harness wrong-connection
attempts remain `OPEN_PENDING_DELIVERED_HARNESS`; nothing here claims physical
proof.

## Switches

Reset and boot select ALPSALPINE `SKRPACE010`, LCSC `C139797`, a
4.2 x 3.2 mm reel-packaged SMD tactile switch using the official SKRP land
pattern. `SKRBACE010` / `C139789` is the exact second reel alternative.
`C72443` is not selected. Both replacement rows still require fresh live
BOM/CPL placement proof; a public LCSC listing alone is insufficient.

## ESP module decision

| Item | WROOM (selected) | MINI (rejected) |
|---|---|---|
| Exact MPN / LCSC | `ESP32-S3-WROOM-1-N8` / `C2913198` | `ESP32-S3-MINI-1-N8` / `C2913206` |
| Body | 18.0 x 25.5 x 3.1 mm | 15.4 x 20.5 x 2.4 mm |
| Pad system | 40 castellated pads plus exposed ground pad 41 | 65-pad LGA; incompatible footprint |
| Native USB | GPIO19 D-, GPIO20 D+ | GPIO19 D-, GPIO20 D+ |
| Flash / RF | 8 MB Quad SPI; onboard PCB antenna | 8 MB Quad SPI; onboard PCB antenna |
| Keepout | Espressif WROOM land-pattern keepout plus Rev C 15 mm enclosure clearance | Espressif MINI land-pattern keepout plus Rev C 15 mm enclosure clearance |
| Decision | retain existing exact part and pad map | `REJECTED_UNQUALIFIED` |

The WROOM audit preserves the existing assignments: GPIO4 `K1_NC_FB`,
GPIO5 `K1_NO_FB`, GPIO6 `TREAD_OK`, GPIO7 `VBUS_PRESENT_N`, GPIO15
`TX_ENABLE`, GPIO16 `PIN3_RX`, GPIO17 `ESP_TX`, GPIO18 `CONS_RX`, GPIO21
`RELAY_CMD`, GPIO38 `STATUS_LED`, GPIO0 boot, GPIO43 UART0 TX, GPIO44 UART0
RX, GPIO19 USB D-, GPIO20 USB D+, and `EN` reset. The machine-readable audit
now enumerates all four straps (GPIO0/3/45/46), unavailable/reserved pins,
every external pull, ADC/digital-drive capabilities, populated or required
decoupling, footprint area, reset/ROM/brownout defaults, and the reset-safe
state for every used signal for both packages.

The repository has no production `firmware/CMakeLists.txt`, exact production
ESP-IDF target/sdkconfig and build artifact, hardware flash/boot/reset/brownout
logs, or MINI-bound safety matrix. The host-only safety model is not production
firmware evidence. Therefore the smaller body does not authorize a module
migration.

## Official references

- Molex Micro-Fit product pages: `430450809`, `430451010`, `430250800`,
  `430251000`, and `430300001`; Molex Micro-Fit 3.0 product specification and
  official sales drawings/CAD.
- Official LCSC pages: `C240838`, `C563827`, `C127351`, `C259745`, `C259786`,
  `C588562`, `C139797`, `C139789`, `C2913198`, and `C2913206`.
- TE Connectivity product page for `1932219-1`.
- Alpha Wire official 3051 specification, including all eight exact color/put-up
  MPNs used by the CSV drawings.
- HellermannTyton official `151-00745` record for PA66 material, UL94 V2,
  retention function, dimensions, and -40 to +85 °C range. No electrical
  voltage rating is assigned to this mechanical part.
- ALPSALPINE SKRP and SKRB official product/land-pattern pages.
- Espressif ESP32-S3-WROOM-1 and ESP32-S3-MINI-1 official datasheets.

Machine-readable URLs, timestamps, stock observations, modeled dimensions,
ratings, and rejection constraints are in `candidates.json` and
`REV-C-PART-SELECTION.json`.

## Exact Task 10 gates

Task 10 must still obtain live BOM/CPL acceptance for every selected SMT row
and a firm finished-harness quote/orderable assembly number covering the TE
jack carrier PCB, enclosure, production drawing, tooling/NRE, continuity test,
and strain-relief installation. Delivered assemblies must pass the modeled
wrong-mating test. RJ45 single-open 2 A, installed voltage drop/thermal
behavior, and the treadmill/USB-ground physical envelope remain open. Until
those gates close, production, deployment, purchase, and turnkey status are
forbidden.

#!/usr/bin/env python3
"""Esp32Tap master design data — single source of truth.

Every component, pin, and net of the ESP32-S3 treadmill tap board is defined
here.  gen_sch.py and gen_pcb.py both consume this file, so the schematic and
the board can never disagree with each other or with NETLIST.md (which is
generated from the same tables by gen_netlist_md.py).

Conventions:
  * Pin numbers are KiCad footprint pad numbers (strings).
  * Nets are {net_name: [(ref, pad), ...]}.
  * NC lists pins deliberately left unconnected (get no_connect markers).
"""

FPLIB = "/usr/share/kicad/footprints"

# ref: (value, footprint_lib, footprint_name, LCSC, jlc_class, unit_cost_usd,
#       description, pins {num: name})
COMPONENTS = {
    # --- Connectors -------------------------------------------------------
    "J1": (
        "RJ45_Console_54602-908LF",
        "Connector_RJ",
        "RJ45_Amphenol_54602-x08_Horizontal",
        "C2847314",
        "Extended-THT",
        0.38,
        "RJ45 jack, unshielded, THT — console-side cable (same jack family as PiZeroHat)",
        {
            str(i): n
            for i, n in enumerate(
                ["GND_A", "P8V_A", "PIN3", "PIN4", "PIN5_SAFETY", "PIN6_CONSOLE", "GND_B", "P8V_B"], start=1
            )
        },
    ),
    "J2": (
        "RJ45_Motor_54602-908LF",
        "Connector_RJ",
        "RJ45_Amphenol_54602-x08_Horizontal",
        "C2847314",
        "Extended-THT",
        0.38,
        "RJ45 jack, unshielded, THT — motor-side cable",
        {
            str(i): n
            for i, n in enumerate(
                ["GND_A", "P8V_A", "PIN3", "PIN4", "PIN5_SAFETY", "PIN6_MOTOR", "GND_B", "P8V_B"], start=1
            )
        },
    ),
    "J3": (
        "USB-C_HRO_TYPE-C-31-M-12",
        "Connector_USB",
        "USB_C_Receptacle_HRO_TYPE-C-31-M-12",
        "C165948",
        "Basic",
        0.16,
        "USB-C receptacle, 16-pin, USB2.0 — native USB flash/JTAG/console",
        {
            "A1": "GND",
            "A4": "VBUS",
            "A5": "CC1",
            "A6": "D+",
            "A7": "D-",
            "A8": "SBU1",
            "A9": "VBUS",
            "A12": "GND",
            "B1": "GND",
            "B4": "VBUS",
            "B5": "CC2",
            "B6": "D+",
            "B7": "D-",
            "B8": "SBU2",
            "B9": "VBUS",
            "B12": "GND",
            "S1": "SHIELD",
        },
    ),
    # --- Actives ----------------------------------------------------------
    "U1": (
        "ESP32-S3-WROOM-1-N8",
        "RF_Module",
        "ESP32-S3-WROOM-1",
        "C2913198",
        "Extended",
        3.20,
        "ESP32-S3 module, 8MB flash, PCB antenna (N8R2 C2913204 is a drop-in "
        "PSRAM upgrade — same footprint; re-quote at order time)",
        {
            "1": "GND",
            "2": "3V3",
            "3": "EN",
            "4": "IO4",
            "5": "IO5",
            "6": "IO6",
            "7": "IO7",
            "8": "IO15",
            "9": "IO16",
            "10": "IO17",
            "11": "IO18",
            "12": "IO8",
            "13": "IO19/USB_D-",
            "14": "IO20/USB_D+",
            "15": "IO3",
            "16": "IO46",
            "17": "IO9",
            "18": "IO10",
            "19": "IO11",
            "20": "IO12",
            "21": "IO13",
            "22": "IO14",
            "23": "IO21",
            "24": "IO47",
            "25": "IO48",
            "26": "IO45",
            "27": "IO0",
            "28": "IO35",
            "29": "IO36",
            "30": "IO37",
            "31": "IO38",
            "32": "IO39",
            "33": "IO40",
            "34": "IO41",
            "35": "IO42",
            "36": "RXD0/IO44",
            "37": "TXD0/IO43",
            "38": "IO2",
            "39": "IO1",
            "40": "GND",
            "41": "GND_EPAD",
        },
    ),
    "U2": (
        "TPS54202DDCR",
        "Package_TO_SOT_SMD",
        "SOT-23-6",
        "C191884",
        "Extended",
        0.35,
        "Buck converter 4.5-28V in, 2A, 500kHz — 8V rail to 3.3V",
        {"1": "GND", "2": "SW", "3": "VIN", "4": "FB", "5": "EN", "6": "BOOT"},
    ),
    "U3": (
        "USBLC6-2SC6",
        "Package_TO_SOT_SMD",
        "SOT-23-6",
        "C7519",
        "Basic",
        0.20,
        "USB ESD protection array",
        {"1": "IO1", "2": "GND", "3": "IO2", "4": "IO2b", "5": "VBUS", "6": "IO1b"},
    ),
    "K1": (
        "G6K-2F-Y-TR DC3",
        "Relay_SMD",
        "Relay_DPDT_Omron_G6K-2F-Y",
        "C2153097",
        "Extended",
        1.50,
        "DPDT signal relay, 3VDC coil (~45mA), SMD — normally-closed pin-6 "
        "fail-safe bypass. Coil 1(+)/8(-); pole A COM=3 NC=2 NO=4; "
        "pole B COM=6 NC=7 NO=5 (Omron G6K datasheet, terminal arrangement)",
        {"1": "COIL+", "2": "NC_A", "3": "COM_A", "4": "NO_A", "5": "NO_B", "6": "COM_B", "7": "NC_B", "8": "COIL-"},
    ),
    "Q1": (
        "S8050",
        "Package_TO_SOT_SMD",
        "SOT-23",
        "C2146",
        "Basic",
        0.02,
        "NPN relay coil driver",
        {"1": "B", "2": "E", "3": "C"},
    ),
    # --- Diodes -----------------------------------------------------------
    "D1": (
        "SS34",
        "Diode_SMD",
        "D_SMA",
        "C8678",
        "Basic",
        0.05,
        "Schottky, 8V-rail leg of VIN ORing",
        {"1": "K", "2": "A"},
    ),
    "D2": (
        "SS34",
        "Diode_SMD",
        "D_SMA",
        "C8678",
        "Basic",
        0.05,
        "Schottky, USB-VBUS leg of VIN ORing (bench flashing without 8V)",
        {"1": "K", "2": "A"},
    ),
    "D3": (
        "SMBJ12A",
        "Diode_SMD",
        "D_SMB",
        "C151251",
        "Extended",
        0.05,
        "TVS, Littelfuse SMBJ12A — unidirectional, 12V standoff / 13.3V "
        "breakdown — input transient clamp on the noisy 8V rail",
        {"1": "K", "2": "A"},
    ),
    "D4": (
        "1N4148WS",
        "Diode_SMD",
        "D_SOD-323",
        "C2128",
        "Basic",
        0.01,
        "Relay coil flyback diode (JSCJ 1N4148WS, SOD-323 — matches footprint)",
        {"1": "K", "2": "A"},
    ),
    "D5": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.10,
        "Bidirectional ESD clamp to GND (NOT rail-referenced — stays inert "
        "when the board is unpowered), console pin-6 line (Nexperia "
        "PESD3V3L1BA,115)",
        {"1": "1", "2": "2"},
    ),
    "D6": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.10,
        "Bidirectional ESD clamp, motor pin-6 line",
        {"1": "1", "2": "2"},
    ),
    "D7": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.10,
        "Bidirectional ESD clamp, pin-3 tap line",
        {"1": "1", "2": "2"},
    ),
    # --- LEDs / switches / fuse / inductor --------------------------------
    "LED1": (
        "XL-1608UGC-04",
        "LED_SMD",
        "LED_0603_1608Metric",
        "C965804",
        "Basic",
        0.02,
        "Status LED (GPIO38)",
        {"1": "K", "2": "A"},
    ),
    "LED2": (
        "RED-0603",
        "LED_SMD",
        "LED_0603_1608Metric",
        "C2286",
        "Basic",
        0.02,
        "3V3 power LED",
        {"1": "K", "2": "A"},
    ),
    "SW1": (
        "KMR2-EN",
        "Button_Switch_SMD",
        "SW_Push_1P1T_NO_CK_KMR2",
        "C72443",
        "Extended",
        0.10,
        "Reset (EN) tactile switch",
        {"1": "A", "2": "B"},
    ),
    "SW2": (
        "KMR2-BOOT",
        "Button_Switch_SMD",
        "SW_Push_1P1T_NO_CK_KMR2",
        "C72443",
        "Extended",
        0.10,
        "Boot (IO0) tactile switch",
        {"1": "A", "2": "B"},
    ),
    "F1": (
        "PolyFuse 0.75A/16V 1206L075/16WR",
        "Fuse",
        "Fuse_1206_3216Metric",
        "C371166",
        "Extended",
        0.10,
        "Resettable fuse on the 8V input (Littelfuse 1206L075/16WR, 0.75A "
        "hold, 16V max — meets the >=16V rating rule; load is ~0.25A at 8V)",
        {"1": "1", "2": "2"},
    ),
    "L1": (
        "10uH SWPA4030S100MT",
        "Inductor_SMD",
        "L_Sunlord_SWPA4030S",
        "C38117",
        "Basic",
        0.07,
        "Buck inductor, 4x4mm shielded, Isat 2.4A (sized for ~0.6A peaks)",
        {"1": "1", "2": "2"},
    ),
    # --- R/C (all 0603) ---------------------------------------------------
    "R1": (
        "100k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25803",
        "Basic",
        0.002,
        "Buck FB divider top (VFB 0.596V -> 3.30V with 22k)",
        {"1": "1", "2": "2"},
    ),
    "R2": (
        "22k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C31850",
        "Basic",
        0.002,
        "Buck FB divider bottom",
        {"1": "1", "2": "2"},
    ),
    "R3": (
        "100k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25803",
        "Basic",
        0.002,
        "Buck EN divider top from VIN (with R14 47k bottom: EN ~2.6V at "
        "7.6V VIN, ~1.6V at 4.7V USB — above the ~1.21V enable threshold, "
        "below the TPS54202 7V EN abs-max; verify thresholds against the "
        "exact datasheet at order time)",
        {"1": "1", "2": "2"},
    ),
    "R14": (
        "47k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25819",
        "Basic",
        0.002,
        "Buck EN divider bottom — keeps EN below the 7V abs-max at "
        "treadmill VIN (a bare pull-up would float EN to ~7.6V)",
        {"1": "1", "2": "2"},
    ),
    "R4": (
        "5.1k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23186",
        "Basic",
        0.002,
        "USB-C CC1 sink resistor",
        {"1": "1", "2": "2"},
    ),
    "R5": (
        "5.1k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23186",
        "Basic",
        0.002,
        "USB-C CC2 sink resistor",
        {"1": "1", "2": "2"},
    ),
    "R6": (
        "100R",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C22775",
        "Basic",
        0.002,
        "Motor-pin6 TX series resistor (drive path; isolated by relay when " "unpowered, so 100R is safe here)",
        {"1": "1", "2": "2"},
    ),
    "R7": (
        "4.7k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23162",
        "Basic",
        0.002,
        "Console-pin6 RX tap series resistor — 4.7k limits unpowered " "GPIO-clamp back-feed to ~0.3mA (gate fix)",
        {"1": "1", "2": "2"},
    ),
    "R8": (
        "4.7k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23162",
        "Basic",
        0.002,
        "Pin-3 tap series resistor (same 0.3mA unpowered back-feed cap)",
        {"1": "1", "2": "2"},
    ),
    "R9": (
        "1k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C21190",
        "Basic",
        0.002,
        "Relay driver base resistor",
        {"1": "1", "2": "2"},
    ),
    "R10": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Relay driver base pull-down (relay stays off during boot/reset)",
        {"1": "1", "2": "2"},
    ),
    "R11": (
        "1k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C21190",
        "Basic",
        0.002,
        "Status LED resistor",
        {"1": "1", "2": "2"},
    ),
    "R12": (
        "2k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C22975",
        "Basic",
        0.002,
        "Power LED resistor",
        {"1": "1", "2": "2"},
    ),
    "R13": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "EN (reset) pull-up",
        {"1": "1", "2": "2"},
    ),
    "C1": (
        "100uF/25V",
        "Capacitor_SMD",
        "CP_Elec_6.3x7.7",
        "C72477",
        "Extended",
        0.10,
        "Input bulk electrolytic, 100uF 25V, 6.3x7.7mm (ROQANG "
        "RVT1E101M0607 — matches the CP_Elec_6.3x7.7 footprint)",
        {"1": "+", "2": "-"},
    ),
    "C2": (
        "4.7uF/50V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C29823",
        "Basic",
        0.04,
        "Input ceramic",
        {"1": "1", "2": "2"},
    ),
    "C3": (
        "4.7uF/50V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C29823",
        "Basic",
        0.04,
        "Buck VIN ceramic (at pin)",
        {"1": "1", "2": "2"},
    ),
    "C4": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "Buck VIN HF bypass",
        {"1": "1", "2": "2"},
    ),
    "C5": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "Buck BOOT cap",
        {"1": "1", "2": "2"},
    ),
    "C6": (
        "22uF/16V 0805",
        "Capacitor_SMD",
        "C_0805_2012Metric",
        "C45783",
        "Basic",
        0.02,
        "Buck output",
        {"1": "1", "2": "2"},
    ),
    "C7": (
        "22uF/16V 0805",
        "Capacitor_SMD",
        "C_0805_2012Metric",
        "C45783",
        "Basic",
        0.02,
        "Buck output",
        {"1": "1", "2": "2"},
    ),
    "C8": (
        "10uF/16V 0805",
        "Capacitor_SMD",
        "C_0805_2012Metric",
        "C15850",
        "Basic",
        0.01,
        "ESP32 3V3 bulk",
        {"1": "1", "2": "2"},
    ),
    "C9": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "ESP32 3V3 HF bypass",
        {"1": "1", "2": "2"},
    ),
    "C10": ("1uF", "Capacitor_SMD", "C_0603_1608Metric", "C15849", "Basic", 0.006, "EN reset RC", {"1": "1", "2": "2"}),
    "C11": ("1uF", "Capacitor_SMD", "C_0603_1608Metric", "C15849", "Basic", 0.006, "VBUS bypass", {"1": "1", "2": "2"}),
    # --- Test points (bare pads, no BOM line) -----------------------------
    "TP1": ("TXD0", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "UART0 TX test pad", {"1": "1"}),
    "TP2": ("RXD0", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "UART0 RX test pad", {"1": "1"}),
    "TP3": ("3V3", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "3V3 test pad", {"1": "1"}),
    "TP4": ("GND", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "GND test pad", {"1": "1"}),
}

# net name -> list of (ref, pad)
NETS = {
    "GND": [
        ("J1", "1"),
        ("J1", "7"),
        ("J2", "1"),
        ("J2", "7"),
        ("J3", "A1"),
        ("J3", "B1"),
        ("J3", "A12"),
        ("J3", "B12"),
        ("J3", "S1"),
        ("U1", "1"),
        ("U1", "40"),
        ("U1", "41"),
        ("U2", "1"),
        ("U3", "2"),
        ("Q1", "2"),
        ("R2", "2"),
        ("R4", "2"),
        ("R5", "2"),
        ("R10", "2"),
        ("R14", "1"),
        ("C1", "2"),
        ("C2", "2"),
        ("C3", "2"),
        ("C4", "2"),
        ("C6", "2"),
        ("C7", "2"),
        ("C8", "2"),
        ("C9", "2"),
        ("C10", "2"),
        ("C11", "2"),
        ("D3", "2"),
        ("D5", "2"),
        ("D6", "2"),
        ("D7", "2"),
        ("LED1", "1"),
        ("LED2", "1"),
        ("SW1", "2"),
        ("SW2", "2"),
        ("TP4", "1"),
    ],
    "+8V_RAW": [("J1", "2"), ("J1", "8"), ("J2", "2"), ("J2", "8"), ("F1", "1")],
    "+8V_F": [("F1", "2"), ("D3", "1"), ("C1", "1"), ("C2", "1"), ("D1", "2")],
    "VIN": [("D1", "1"), ("D2", "1"), ("U2", "3"), ("C3", "1"), ("C4", "1"), ("R3", "1")],
    "BUCK_EN": [("U2", "5"), ("R3", "2"), ("R14", "2")],
    "SW_NODE": [("U2", "2"), ("L1", "1"), ("C5", "2")],
    "BST": [("U2", "6"), ("C5", "1")],
    "+3V3": [
        ("L1", "2"),
        ("C6", "1"),
        ("C7", "1"),
        ("R1", "1"),
        ("U1", "2"),
        ("C8", "1"),
        ("C9", "1"),
        ("R12", "1"),
        ("R13", "1"),
        ("K1", "1"),
        ("D4", "1"),
        ("TP3", "1"),
    ],
    "FB": [("U2", "4"), ("R1", "2"), ("R2", "1")],
    "VBUS": [("J3", "A4"), ("J3", "B4"), ("J3", "A9"), ("J3", "B9"), ("D2", "2"), ("U3", "5"), ("C11", "1")],
    "USB_DN": [("J3", "A7"), ("J3", "B7"), ("U3", "1")],
    "USB_DP": [("J3", "A6"), ("J3", "B6"), ("U3", "3")],
    "USB_DN_MCU": [("U3", "6"), ("U1", "13")],
    "USB_DP_MCU": [("U3", "4"), ("U1", "14")],
    "CC1": [("J3", "A5"), ("R4", "1")],
    "CC2": [("J3", "B5"), ("R5", "1")],
    # Pin 6 — cut through relay + MCU.  De-energized: CONS6==MOT6 (passive
    # stock treadmill, ESP TX physically disconnected).  Energized: ESP TX
    # drives MOT6, console line released (only the 4.7k RX tap remains).
    "CONS6": [("J1", "6"), ("K1", "2"), ("K1", "7"), ("R7", "1"), ("D5", "1")],
    "MOT6": [("J2", "6"), ("K1", "3"), ("K1", "6"), ("D6", "1")],
    "TX_DRV": [("K1", "4"), ("K1", "5"), ("R6", "2")],
    "ESP_TX": [("R6", "1"), ("U1", "10")],  # IO17 = UART1 TX
    "CONS_RX": [("R7", "2"), ("U1", "11")],  # IO18 = UART1 RX
    "PIN3": [("J1", "3"), ("J2", "3"), ("R8", "1"), ("D7", "1")],
    "PIN3_RX": [("R8", "2"), ("U1", "9")],  # IO16 = UART2 RX
    "PIN4_PASS": [("J1", "4"), ("J2", "4")],
    "PIN5_SAFETY": [("J1", "5"), ("J2", "5")],
    "RELAY_SW": [("K1", "8"), ("Q1", "3"), ("D4", "2")],
    "Q1_B": [("Q1", "1"), ("R9", "2"), ("R10", "1")],
    "RELAY_EN": [("R9", "1"), ("U1", "23")],  # IO21
    "STATUS_LED": [("U1", "31"), ("R11", "1")],  # IO38
    "LED1_A": [("R11", "2"), ("LED1", "2")],
    "LED2_A": [("R12", "2"), ("LED2", "2")],
    "EN": [("U1", "3"), ("R13", "2"), ("C10", "1"), ("SW1", "1")],
    "IO0": [("U1", "27"), ("SW2", "1")],
    "U0TXD": [("U1", "37"), ("TP1", "1")],
    "U0RXD": [("U1", "36"), ("TP2", "1")],
}

# Deliberately unconnected pins (schematic no_connect markers)
NC = [
    ("U1", n)
    for n in [
        "4",
        "5",
        "6",
        "7",
        "8",
        "12",
        "15",
        "16",
        "17",
        "18",
        "19",
        "20",
        "21",
        "22",
        "24",
        "25",
        "26",
        "28",
        "29",
        "30",
        "32",
        "33",
        "34",
        "35",
        "38",
        "39",
    ]
] + [("J3", "A8"), ("J3", "B8")]


def validate():
    used = {}
    for net, pads in NETS.items():
        for ref, pad in pads:
            assert ref in COMPONENTS, f"unknown ref {ref}"
            assert pad in COMPONENTS[ref][7], f"{ref}.{pad} not a defined pin"
            key = (ref, pad)
            assert key not in used, f"{ref}.{pad} in both {used[key]} and {net}"
            used[key] = net
    for ref, pad in NC:
        key = (ref, pad)
        assert key not in used, f"NC pin {ref}.{pad} also in net {used[key]}"
        used[key] = "<NC>"
    missing = []
    for ref, comp in COMPONENTS.items():
        for pad in comp[7]:
            if (ref, pad) not in used:
                missing.append(f"{ref}.{pad}")
    assert not missing, f"pins with no net and no NC: {missing}"
    # every net must have at least 2 pins
    for net, pads in NETS.items():
        assert len(pads) >= 2, f"net {net} has <2 pins"
    return True


if __name__ == "__main__":
    validate()
    npins = sum(len(c[7]) for c in COMPONENTS.values())
    print(f"OK: {len(COMPONENTS)} components, {npins} pins, " f"{len(NETS)} nets, {len(NC)} no-connects")

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

from types import MappingProxyType

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
        "Extended",
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
        "Extended",
        0.20,
        "USB ESD protection array",
        {"1": "IO1", "2": "GND", "3": "IO2", "4": "IO2b", "5": "VBUS", "6": "IO1b"},
    ),
    "U4": (
        "TPS3700DDCR",
        "Package_TO_SOT_SMD",
        "SOT-23-6",
        "C33002",
        "Extended",
        0.78,
        "Window voltage supervisor with open-drain outputs",
        {"1": "OUTA", "2": "GND", "3": "INA+", "4": "INB-", "5": "VDD", "6": "OUTB"},
    ),
    "U5": (
        "TPS70950DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C96028",
        "Extended",
        0.42,
        "5V 150mA LDO with enable for relay-coil supply",
        {"1": "IN", "2": "GND", "3": "EN", "4": "NC", "5": "OUT"},
    ),
    "U6": (
        "SN74LVC2G08DCTR",
        "Package_SO",
        "SSOP-8_2.95x2.8mm_P0.65mm",
        "C352973",
        "Extended",
        0.16,
        "Dual two-input AND gate with partial-power-down support",
        {"1": "1A", "2": "1B", "3": "2Y", "4": "GND", "5": "2A", "6": "2B", "7": "1Y", "8": "VCC"},
    ),
    "U7": (
        "SN74LVC1G126DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C7834",
        "Extended",
        0.11,
        "Single tri-state buffer with partial-power-down support",
        {"1": "OE", "2": "A", "3": "GND", "4": "Y", "5": "VCC"},
    ),
    "K1": (
        "G6K-2F-Y-TR DC5",
        "Relay_SMD",
        "Relay_DPDT_Omron_G6K-2F-Y",
        "C47190",
        "Extended",
        1.50,
        "DPDT signal relay, 5VDC coil, SMD — pole A provides fail-safe "
        "serial bypass and pole B provides dry-contact armature feedback",
        {"1": "COIL+", "2": "NC_A", "3": "COM_A", "4": "NO_A", "5": "NO_B", "6": "COM_B", "7": "NC_B", "8": "COIL-"},
    ),
    "Q1": (
        "BC817-40,215",
        "Package_TO_SOT_SMD",
        "SOT-23",
        "C52801",
        "Extended",
        0.03,
        "45V NPN relay coil driver",
        {"1": "B", "2": "E", "3": "C"},
    ),
    "Q2": (
        "2N7002",
        "Package_TO_SOT_SMD",
        "SOT-23",
        "C8545",
        "Basic",
        0.02,
        "N-channel MOSFET, active-low VBUS-present detector",
        {"1": "G", "2": "S", "3": "D"},
    ),
    # --- Diodes -----------------------------------------------------------
    "D1": (
        "SS34",
        "Diode_SMD",
        "D_SMA",
        "C8678",
        "Basic",
        0.05,
        "Series Schottky providing reverse-polarity protection for local VIN",
        {"1": "K", "2": "A"},
    ),
    "D3": (
        "SMBJ10A",
        "Diode_SMD",
        "D_SMB",
        "C151250",
        "Extended",
        0.05,
        "TVS, Littelfuse SMBJ10A — protected-VIN transient clamp",
        {"1": "K", "2": "A"},
    ),
    "D4": (
        "SMAJ6.0CA",
        "Diode_SMD",
        "D_SMA",
        "C80275",
        "Extended",
        0.05,
        "Bidirectional TVS directly across the relay coil for fast release",
        {"1": "K", "2": "A"},
    ),
    "D5": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.061,
        "Bidirectional ESD clamp to GND (NOT rail-referenced — stays inert "
        "when the board is unpowered), console pin-6 line (BORN PESD3V3L1BA-N; "
        "drop-in for Nexperia C51450 which showed 0 stock at validation "
        "2026-07-23)",
        {"1": "1", "2": "2"},
    ),
    "D6": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.061,
        "Bidirectional ESD clamp, motor pin-6 line",
        {"1": "1", "2": "2"},
    ),
    "D7": (
        "PESD3V3L1BA-N",
        "Diode_SMD",
        "D_SOD-323",
        "C316020",
        "Extended",
        0.061,
        "Bidirectional ESD clamp, pin-3 tap line",
        {"1": "1", "2": "2"},
    ),
    # --- LEDs / switches / fuse / inductor --------------------------------
    "LED1": (
        "XL-1608UGC-04",
        "LED_SMD",
        "LED_0603_1608Metric",
        "C965804",
        "Extended",
        0.005,
        "Status LED green (GPIO38; XL-1608UGC-04, 5.2M stock at validation " "2026-07-23; replaces C72043 stock=6)",
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
        "1812L075/24DR",
        "Fuse",
        "Fuse_1812_4532Metric",
        "C207065",
        "Extended",
        0.10,
        "Resettable fuse on the local 8V branch, 0.75A hold, 24V maximum",
        {"1": "1", "2": "2"},
    ),
    "L1": (
        "10uH SWPA4030S100MT",
        "Inductor_SMD",
        "L_Sunlord_SWPA4030S",
        "C38117",
        "Extended",
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
        "Buck EN divider top from protected VIN (with R14 47k bottom: "
        "EN remains above the enable threshold at the approved VIN window "
        "and below the TPS54202 7V EN absolute maximum)",
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
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Console-pin6 RX tap series resistor — limits unpowered GPIO-clamp back-feed",
        {"1": "1", "2": "2"},
    ),
    "R8": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Pin-3 tap series resistor — limits unpowered GPIO-clamp back-feed",
        {"1": "1", "2": "2"},
    ),
    "R9": (
        "560R",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23204",
        "Basic",
        0.002,
        "Relay driver base resistor (forced beta <= 10 at conservative drive)",
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
        "330R",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23138",
        "Basic",
        0.002,
        "Status LED resistor (330R: 1.51mA typ through LED1 — validation " "fix F1; 1k gave a sub-visible 0.6mA)",
        {"1": "1", "2": "2"},
    ),
    "R12": (
        "1k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C21190",
        "Basic",
        0.002,
        "Power LED resistor (1k for a clearly visible power light; was 2k " "at 0.78mA)",
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
    "R15": (
        "22R",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23345",
        "Basic",
        0.002,
        "USB D- series termination",
        {"1": "1", "2": "2"},
    ),
    "R16": (
        "22R",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23345",
        "Basic",
        0.002,
        "USB D+ series termination",
        {"1": "1", "2": "2"},
    ),
    "R17": (
        "150k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C22807",
        "Basic",
        0.002,
        "Undervoltage-sense divider top, 1%",
        {"1": "1", "2": "2"},
    ),
    "R18": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Undervoltage-sense divider bottom, 1%",
        {"1": "1", "2": "2"},
    ),
    "R19": (
        "255k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C23354",
        "Extended",
        0.002,
        "Overvoltage-sense divider top, 1%",
        {"1": "1", "2": "2"},
    ),
    "R20": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Overvoltage-sense divider bottom, 1%",
        {"1": "1", "2": "2"},
    ),
    "R21": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "TREAD_OK pull-up",
        {"1": "1", "2": "2"},
    ),
    "R22": (
        "100k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25803",
        "Basic",
        0.002,
        "TREAD_OK power-off pull-down",
        {"1": "1", "2": "2"},
    ),
    "R23": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "RELAY_CMD pull-down",
        {"1": "1", "2": "2"},
    ),
    "R24": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "RELAY_GATE pull-down at U5 enable",
        {"1": "1", "2": "2"},
    ),
    "R25": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Relay normally-closed feedback pull-up",
        {"1": "1", "2": "2"},
    ),
    "R26": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "Relay normally-open feedback pull-up",
        {"1": "1", "2": "2"},
    ),
    "R27": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "TX_ENABLE pull-down",
        {"1": "1", "2": "2"},
    ),
    "R28": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "TX_GATE pull-down at U7 output enable",
        {"1": "1", "2": "2"},
    ),
    "R29": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "USB VBUS discharge",
        {"1": "1", "2": "2"},
    ),
    "R30": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "VBUS_PRESENT_N pull-up",
        {"1": "1", "2": "2"},
    ),
    "R31": (
        "10k",
        "Resistor_SMD",
        "R_0603_1608Metric",
        "C25804",
        "Basic",
        0.002,
        "GPIO0 boot pull-up",
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
        "10uF/25V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C14860",
        "Extended",
        0.04,
        "Input ceramic",
        {"1": "1", "2": "2"},
    ),
    "C3": (
        "10uF/25V X7R 1206",
        "Capacitor_SMD",
        "C_1206_3216Metric",
        "C14860",
        "Extended",
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
        "22uF/25V X7R 1210",
        "Capacitor_SMD",
        "C_1210_3225Metric",
        "C2918511",
        "Extended",
        0.08,
        "Buck output",
        {"1": "1", "2": "2"},
    ),
    "C7": (
        "22uF/25V X7R 1210",
        "Capacitor_SMD",
        "C_1210_3225Metric",
        "C2918511",
        "Extended",
        0.08,
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
    "C11": ("100nF", "Capacitor_SMD", "C_0603_1608Metric", "C14663", "Basic", 0.004, "VBUS bypass", {"1": "1", "2": "2"}),
    "C12": (
        "56pF C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C39148",
        "Extended",
        0.004,
        "Buck feed-forward capacitor directly across R1",
        {"1": "1", "2": "2"},
    ),
    "C13": (
        "DNP",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "",
        "DNP",
        0,
        "Optional USB D- shunt-tuning footprint; do not populate",
        {"1": "1", "2": "2"},
    ),
    "C14": (
        "DNP",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "",
        "DNP",
        0,
        "Optional USB D+ shunt-tuning footprint; do not populate",
        {"1": "1", "2": "2"},
    ),
    "C15": (
        "1uF/25V X7R 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C106858",
        "Extended",
        0.01,
        "U5 input bypass",
        {"1": "1", "2": "2"},
    ),
    "C16": (
        "4.7uF/25V X7R 0805",
        "Capacitor_SMD",
        "C_0805_2012Metric",
        "C354262",
        "Extended",
        0.02,
        "U5 relay-supply output capacitor",
        {"1": "1", "2": "2"},
    ),
    "C17": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "U4 VIN decoupling",
        {"1": "1", "2": "2"},
    ),
    "C18": (
        "1nF/50V C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C342541",
        "Extended",
        0.004,
        "Undervoltage-sense filter",
        {"1": "1", "2": "2"},
    ),
    "C19": (
        "1nF/50V C0G 0603",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C342541",
        "Extended",
        0.004,
        "Overvoltage-sense filter",
        {"1": "1", "2": "2"},
    ),
    "C20": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "U6 logic-supply decoupling",
        {"1": "1", "2": "2"},
    ),
    "C21": (
        "100nF",
        "Capacitor_SMD",
        "C_0603_1608Metric",
        "C14663",
        "Basic",
        0.004,
        "U7 buffer-supply decoupling",
        {"1": "1", "2": "2"},
    ),
    # --- Test points (bare pads, no BOM line) -----------------------------
    "TP1": ("TXD0", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "UART0 TX test pad", {"1": "1"}),
    "TP2": ("RXD0", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "UART0 RX test pad", {"1": "1"}),
    "TP3": ("3V3", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "3V3 test pad", {"1": "1"}),
    "TP4": ("GND", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "GND test pad", {"1": "1"}),
    "TP5": ("VIN", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Protected VIN test pad", {"1": "1"}),
    "TP6": ("+5V_RLY", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Relay-supply test pad", {"1": "1"}),
    "TP7": ("TREAD_OK", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Voltage-window permission test pad", {"1": "1"}),
    "TP8": ("RELAY_GATE", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Hardware relay-gate test pad", {"1": "1"}),
    "TP9": ("RELAY_SW", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Relay transistor collector test pad", {"1": "1"}),
    "TP10": ("TX_GATE", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Hardware transmit-gate test pad", {"1": "1"}),
    "TP11": ("TX_DRV", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Relay transmit-drive test pad", {"1": "1"}),
    "TP12": ("K1_NC_FB", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Relay normally-closed feedback test pad", {"1": "1"}),
    "TP13": ("K1_NO_FB", "TestPoint", "TestPoint_Pad_1.5x1.5mm", "", "none", 0, "Relay normally-open feedback test pad", {"1": "1"}),
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
        ("U4", "2"),
        ("U5", "2"),
        ("U6", "4"),
        ("U7", "3"),
        ("K1", "6"),
        ("Q1", "2"),
        ("Q2", "2"),
        ("R2", "2"),
        ("R4", "2"),
        ("R5", "2"),
        ("R10", "2"),
        ("R14", "1"),
        ("R18", "2"),
        ("R20", "2"),
        ("R22", "2"),
        ("R23", "2"),
        ("R24", "2"),
        ("R27", "2"),
        ("R28", "2"),
        ("R29", "2"),
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
        ("C13", "2"),
        ("C14", "2"),
        ("C15", "2"),
        ("C16", "2"),
        ("C17", "2"),
        ("C18", "2"),
        ("C19", "2"),
        ("C20", "2"),
        ("C21", "2"),
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
    "+8V_F": [("F1", "2"), ("D1", "2")],
    "VIN": [
        ("D1", "1"),
        ("D3", "1"),
        ("U2", "3"),
        ("U4", "5"),
        ("U5", "1"),
        ("C1", "1"),
        ("C2", "1"),
        ("C3", "1"),
        ("C4", "1"),
        ("C15", "1"),
        ("C17", "1"),
        ("R3", "1"),
        ("R17", "1"),
        ("R19", "1"),
        ("TP5", "1"),
    ],
    "BUCK_EN": [("U2", "5"), ("R3", "2"), ("R14", "2")],
    "SW_NODE": [("U2", "2"), ("L1", "1"), ("C5", "2")],
    "BST": [("U2", "6"), ("C5", "1")],
    "+3V3": [
        ("L1", "2"),
        ("C6", "1"),
        ("C7", "1"),
        ("R1", "1"),
        ("C12", "1"),
        ("U1", "2"),
        ("C8", "1"),
        ("C9", "1"),
        ("R12", "1"),
        ("R13", "1"),
        ("U6", "8"),
        ("U7", "5"),
        ("C20", "1"),
        ("C21", "1"),
        ("R21", "1"),
        ("R25", "1"),
        ("R26", "1"),
        ("R30", "1"),
        ("R31", "1"),
        ("TP3", "1"),
    ],
    "FB": [("U2", "4"), ("R1", "2"), ("R2", "1"), ("C12", "2")],
    "VBUS": [
        ("J3", "A4"),
        ("J3", "B4"),
        ("J3", "A9"),
        ("J3", "B9"),
        ("U3", "5"),
        ("C11", "1"),
        ("R29", "1"),
        ("Q2", "1"),
    ],
    "USB_DN": [("J3", "A7"), ("J3", "B7"), ("U3", "1")],
    "USB_DP": [("J3", "A6"), ("J3", "B6"), ("U3", "3")],
    "USB_DN_MCU": [("U3", "6"), ("R15", "1")],
    "USB_DP_MCU": [("U3", "4"), ("R16", "1")],
    "USB_DN_R": [("R15", "2"), ("C13", "1"), ("U1", "13")],
    "USB_DP_R": [("R16", "2"), ("C14", "1"), ("U1", "14")],
    "CC1": [("J3", "A5"), ("R4", "1")],
    "CC2": [("J3", "B5"), ("R5", "1")],
    # Pole A is the only serial-transfer pole.  De-energized, CONS6 is
    # joined to MOT6 without requiring board power; energized, TX_DRV is
    # joined to MOT6 while the console line remains receive-only.
    "CONS6": [("J1", "6"), ("K1", "2"), ("R7", "1"), ("D5", "1")],
    "MOT6": [("J2", "6"), ("K1", "3"), ("D6", "1")],
    "TX_DRV": [("K1", "4"), ("R6", "2"), ("TP11", "1")],
    "TX_BUF": [("U7", "4"), ("R6", "1")],
    "ESP_TX": [("U7", "2"), ("U1", "10")],  # IO17 = UART1 TX
    "CONS_RX": [("R7", "2"), ("U1", "11")],  # IO18 = UART1 RX
    "PIN3": [("J1", "3"), ("J2", "3"), ("R8", "1"), ("D7", "1")],
    "PIN3_RX": [("R8", "2"), ("U1", "9")],  # IO16 = UART2 RX
    "PIN4_PASS": [("J1", "4"), ("J2", "4")],
    "PIN5_SAFETY": [("J1", "5"), ("J2", "5")],
    "UV_SENSE": [("U4", "3"), ("R17", "2"), ("R18", "1"), ("C18", "1")],
    "OV_SENSE": [("U4", "4"), ("R19", "2"), ("R20", "1"), ("C19", "1")],
    "TREAD_OK": [
        ("U4", "1"),
        ("U4", "6"),
        ("U1", "6"),
        ("U6", "2"),
        ("U6", "6"),
        ("R21", "2"),
        ("R22", "1"),
        ("TP7", "1"),
    ],
    "RELAY_CMD": [("U1", "23"), ("U6", "1"), ("R23", "1")],
    "RELAY_GATE": [("U6", "7"), ("U5", "3"), ("R9", "1"), ("R24", "1"), ("TP8", "1")],
    "+5V_RLY": [("U5", "5"), ("C16", "1"), ("K1", "1"), ("D4", "1"), ("TP6", "1")],
    "RELAY_SW": [("K1", "8"), ("Q1", "3"), ("D4", "2"), ("TP9", "1")],
    "Q1_B": [("Q1", "1"), ("R9", "2"), ("R10", "1")],
    "K1_NC_FB": [("K1", "7"), ("R25", "2"), ("U1", "4"), ("TP12", "1")],
    "K1_NO_FB": [("K1", "5"), ("R26", "2"), ("U1", "5"), ("TP13", "1")],
    "TX_ENABLE": [("U1", "8"), ("U6", "5"), ("R27", "1")],
    "TX_GATE": [("U6", "3"), ("U7", "1"), ("R28", "1"), ("TP10", "1")],
    "VBUS_PRESENT_N": [("Q2", "3"), ("R30", "2"), ("U1", "7")],
    "STATUS_LED": [("U1", "31"), ("R11", "1")],  # IO38
    "LED1_A": [("R11", "2"), ("LED1", "2")],
    "LED2_A": [("R12", "2"), ("LED2", "2")],
    "EN": [("U1", "3"), ("R13", "2"), ("C10", "1"), ("SW1", "1")],
    "IO0": [("U1", "27"), ("SW2", "1"), ("R31", "2")],
    "U0TXD": [("U1", "37"), ("TP1", "1")],
    "U0RXD": [("U1", "36"), ("TP2", "1")],
}

# Deliberately unconnected pins (schematic no_connect markers)
NC = [
    ("U1", n)
    for n in [
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
] + [("J3", "A8"), ("J3", "B8"), ("U5", "4")]

DNP = {"C13", "C14"}

# KiCad-compatible symbol electrical types for every physical pad.  Most
# discrete parts and connector contacts are passive; the overrides preserve
# the active directions that matter to ERC and to the fail-safe architecture.
_PIN_TYPE_OVERRIDES = {
    ("U1", "1"): "power_in",
    ("U1", "2"): "power_in",
    ("U1", "3"): "input",
    ("U1", "4"): "input",
    ("U1", "5"): "input",
    ("U1", "6"): "input",
    ("U1", "7"): "input",
    ("U1", "8"): "output",
    ("U1", "9"): "input",
    ("U1", "10"): "output",
    ("U1", "11"): "input",
    ("U1", "13"): "bidirectional",
    ("U1", "14"): "bidirectional",
    ("U1", "23"): "output",
    ("U1", "27"): "input",
    ("U1", "31"): "output",
    ("U1", "36"): "input",
    ("U1", "37"): "output",
    ("U1", "40"): "power_in",
    ("U1", "41"): "power_in",
    ("U2", "1"): "power_in",
    ("U2", "2"): "power_out",
    ("U2", "3"): "power_in",
    ("U2", "4"): "input",
    ("U2", "5"): "input",
    ("U2", "6"): "passive",
    ("U3", "1"): "bidirectional",
    ("U3", "2"): "power_in",
    ("U3", "3"): "bidirectional",
    ("U3", "4"): "bidirectional",
    ("U3", "5"): "power_in",
    ("U3", "6"): "bidirectional",
    ("U4", "1"): "open_collector",
    ("U4", "2"): "power_in",
    ("U4", "3"): "input",
    ("U4", "4"): "input",
    ("U4", "5"): "power_in",
    ("U4", "6"): "open_collector",
    ("U5", "1"): "power_in",
    ("U5", "2"): "power_in",
    ("U5", "3"): "input",
    ("U5", "4"): "no_connect",
    ("U5", "5"): "power_out",
    ("U6", "1"): "input",
    ("U6", "2"): "input",
    ("U6", "3"): "output",
    ("U6", "4"): "power_in",
    ("U6", "5"): "input",
    ("U6", "6"): "input",
    ("U6", "7"): "output",
    ("U6", "8"): "power_in",
    ("U7", "1"): "input",
    ("U7", "2"): "input",
    ("U7", "3"): "power_in",
    ("U7", "4"): "tri_state",
    ("U7", "5"): "power_in",
    ("Q1", "1"): "input",
    ("Q1", "3"): "open_collector",
    ("Q2", "1"): "input",
    ("Q2", "3"): "open_collector",
}
_PIN_TYPE_OVERRIDES.update({pin: "no_connect" for pin in NC})
_PIN_TYPE_OVERRIDES = MappingProxyType(
    dict(_PIN_TYPE_OVERRIDES)
)

# Independent validation oracle.  Keep this literal separate from the table
# used to construct PIN_TYPES so changing both the derived output and its
# construction overrides cannot silently weaken ERC semantics.
_ACTIVE_PIN_TYPE_LOCKS = MappingProxyType({
    ("U1", "1"): "power_in",
    ("U1", "2"): "power_in",
    ("U1", "3"): "input",
    ("U1", "4"): "input",
    ("U1", "5"): "input",
    ("U1", "6"): "input",
    ("U1", "7"): "input",
    ("U1", "8"): "output",
    ("U1", "9"): "input",
    ("U1", "10"): "output",
    ("U1", "11"): "input",
    ("U1", "12"): "no_connect",
    ("U1", "13"): "bidirectional",
    ("U1", "14"): "bidirectional",
    ("U1", "15"): "no_connect",
    ("U1", "16"): "no_connect",
    ("U1", "17"): "no_connect",
    ("U1", "18"): "no_connect",
    ("U1", "19"): "no_connect",
    ("U1", "20"): "no_connect",
    ("U1", "21"): "no_connect",
    ("U1", "22"): "no_connect",
    ("U1", "23"): "output",
    ("U1", "24"): "no_connect",
    ("U1", "25"): "no_connect",
    ("U1", "26"): "no_connect",
    ("U1", "27"): "input",
    ("U1", "28"): "no_connect",
    ("U1", "29"): "no_connect",
    ("U1", "30"): "no_connect",
    ("U1", "31"): "output",
    ("U1", "32"): "no_connect",
    ("U1", "33"): "no_connect",
    ("U1", "34"): "no_connect",
    ("U1", "35"): "no_connect",
    ("U1", "36"): "input",
    ("U1", "37"): "output",
    ("U1", "38"): "no_connect",
    ("U1", "39"): "no_connect",
    ("U1", "40"): "power_in",
    ("U1", "41"): "power_in",
    ("U2", "1"): "power_in",
    ("U2", "2"): "power_out",
    ("U2", "3"): "power_in",
    ("U2", "4"): "input",
    ("U2", "5"): "input",
    ("U2", "6"): "passive",
    ("U3", "1"): "bidirectional",
    ("U3", "2"): "power_in",
    ("U3", "3"): "bidirectional",
    ("U3", "4"): "bidirectional",
    ("U3", "5"): "power_in",
    ("U3", "6"): "bidirectional",
    ("U4", "1"): "open_collector",
    ("U4", "2"): "power_in",
    ("U4", "3"): "input",
    ("U4", "4"): "input",
    ("U4", "5"): "power_in",
    ("U4", "6"): "open_collector",
    ("U5", "1"): "power_in",
    ("U5", "2"): "power_in",
    ("U5", "3"): "input",
    ("U5", "4"): "no_connect",
    ("U5", "5"): "power_out",
    ("U6", "1"): "input",
    ("U6", "2"): "input",
    ("U6", "3"): "output",
    ("U6", "4"): "power_in",
    ("U6", "5"): "input",
    ("U6", "6"): "input",
    ("U6", "7"): "output",
    ("U6", "8"): "power_in",
    ("U7", "1"): "input",
    ("U7", "2"): "input",
    ("U7", "3"): "power_in",
    ("U7", "4"): "tri_state",
    ("U7", "5"): "power_in",
    ("Q1", "1"): "input",
    ("Q1", "3"): "open_collector",
    ("Q2", "1"): "input",
    ("Q2", "3"): "open_collector",
    ("J3", "A8"): "no_connect",
    ("J3", "B8"): "no_connect",
})

PIN_TYPES = {
    (ref, pad): _PIN_TYPE_OVERRIDES.get((ref, pad), "passive")
    for ref, component in COMPONENTS.items()
    for pad in component[7]
}

_TWO_PIN_LOCK = {"1": "1", "2": "2"}
_PART_LOCKS = {
    "F1": ("1812L075/24DR", "Fuse", "Fuse_1812_4532Metric", "C207065", _TWO_PIN_LOCK),
    "D3": ("SMBJ10A", "Diode_SMD", "D_SMB", "C151250", {"1": "K", "2": "A"}),
    "D4": ("SMAJ6.0CA", "Diode_SMD", "D_SMA", "C80275", {"1": "K", "2": "A"}),
    "K1": (
        "G6K-2F-Y-TR DC5",
        "Relay_SMD",
        "Relay_DPDT_Omron_G6K-2F-Y",
        "C47190",
        {"1": "COIL+", "2": "NC_A", "3": "COM_A", "4": "NO_A", "5": "NO_B", "6": "COM_B", "7": "NC_B", "8": "COIL-"},
    ),
    "Q1": ("BC817-40,215", "Package_TO_SOT_SMD", "SOT-23", "C52801", {"1": "B", "2": "E", "3": "C"}),
    "U4": (
        "TPS3700DDCR",
        "Package_TO_SOT_SMD",
        "SOT-23-6",
        "C33002",
        {"1": "OUTA", "2": "GND", "3": "INA+", "4": "INB-", "5": "VDD", "6": "OUTB"},
    ),
    "U5": (
        "TPS70950DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C96028",
        {"1": "IN", "2": "GND", "3": "EN", "4": "NC", "5": "OUT"},
    ),
    "U6": (
        "SN74LVC2G08DCTR",
        "Package_SO",
        "SSOP-8_2.95x2.8mm_P0.65mm",
        "C352973",
        {"1": "1A", "2": "1B", "3": "2Y", "4": "GND", "5": "2A", "6": "2B", "7": "1Y", "8": "VCC"},
    ),
    "U7": (
        "SN74LVC1G126DBVR",
        "Package_TO_SOT_SMD",
        "SOT-23-5",
        "C7834",
        {"1": "OE", "2": "A", "3": "GND", "4": "Y", "5": "VCC"},
    ),
    "Q2": ("2N7002", "Package_TO_SOT_SMD", "SOT-23", "C8545", {"1": "G", "2": "S", "3": "D"}),
    "C2": ("10uF/25V X7R 1206", "Capacitor_SMD", "C_1206_3216Metric", "C14860", _TWO_PIN_LOCK),
    "C3": ("10uF/25V X7R 1206", "Capacitor_SMD", "C_1206_3216Metric", "C14860", _TWO_PIN_LOCK),
    "C6": ("22uF/25V X7R 1210", "Capacitor_SMD", "C_1210_3225Metric", "C2918511", _TWO_PIN_LOCK),
    "C7": ("22uF/25V X7R 1210", "Capacitor_SMD", "C_1210_3225Metric", "C2918511", _TWO_PIN_LOCK),
    "C12": ("56pF C0G 0603", "Capacitor_SMD", "C_0603_1608Metric", "C39148", _TWO_PIN_LOCK),
    "C13": ("DNP", "Capacitor_SMD", "C_0603_1608Metric", "", _TWO_PIN_LOCK),
    "C14": ("DNP", "Capacitor_SMD", "C_0603_1608Metric", "", _TWO_PIN_LOCK),
    "C15": ("1uF/25V X7R 0603", "Capacitor_SMD", "C_0603_1608Metric", "C106858", _TWO_PIN_LOCK),
    "C16": ("4.7uF/25V X7R 0805", "Capacitor_SMD", "C_0805_2012Metric", "C354262", _TWO_PIN_LOCK),
    "C17": ("100nF", "Capacitor_SMD", "C_0603_1608Metric", "C14663", _TWO_PIN_LOCK),
    "C18": ("1nF/50V C0G 0603", "Capacitor_SMD", "C_0603_1608Metric", "C342541", _TWO_PIN_LOCK),
    "C19": ("1nF/50V C0G 0603", "Capacitor_SMD", "C_0603_1608Metric", "C342541", _TWO_PIN_LOCK),
    "C20": ("100nF", "Capacitor_SMD", "C_0603_1608Metric", "C14663", _TWO_PIN_LOCK),
    "C21": ("100nF", "Capacitor_SMD", "C_0603_1608Metric", "C14663", _TWO_PIN_LOCK),
}
_PART_LOCKS = MappingProxyType({
    ref: (
        lock[0],
        lock[1],
        lock[2],
        lock[3],
        tuple(sorted(lock[4].items())),
    )
    for ref, lock in _PART_LOCKS.items()
})
del _TWO_PIN_LOCK

_NET_LOCKS = {
    "+8V_RAW": {("J1", "2"), ("J1", "8"), ("J2", "2"), ("J2", "8"), ("F1", "1")},
    "+8V_F": {("F1", "2"), ("D1", "2")},
    "VIN": {
        ("D1", "1"),
        ("D3", "1"),
        ("U2", "3"),
        ("U4", "5"),
        ("U5", "1"),
        ("C1", "1"),
        ("C2", "1"),
        ("C3", "1"),
        ("C4", "1"),
        ("C15", "1"),
        ("C17", "1"),
        ("R3", "1"),
        ("R17", "1"),
        ("R19", "1"),
        ("TP5", "1"),
    },
    "VBUS": {
        ("J3", "A4"),
        ("J3", "A9"),
        ("J3", "B4"),
        ("J3", "B9"),
        ("U3", "5"),
        ("C11", "1"),
        ("R29", "1"),
        ("Q2", "1"),
    },
    "USB_DN_MCU": {("U3", "6"), ("R15", "1")},
    "USB_DP_MCU": {("U3", "4"), ("R16", "1")},
    "USB_DN_R": {("R15", "2"), ("C13", "1"), ("U1", "13")},
    "USB_DP_R": {("R16", "2"), ("C14", "1"), ("U1", "14")},
    "UV_SENSE": {("U4", "3"), ("R17", "2"), ("R18", "1"), ("C18", "1")},
    "OV_SENSE": {("U4", "4"), ("R19", "2"), ("R20", "1"), ("C19", "1")},
    "TREAD_OK": {
        ("U4", "1"),
        ("U4", "6"),
        ("U1", "6"),
        ("U6", "2"),
        ("U6", "6"),
        ("R21", "2"),
        ("R22", "1"),
        ("TP7", "1"),
    },
    "RELAY_CMD": {("U1", "23"), ("U6", "1"), ("R23", "1")},
    "RELAY_GATE": {("U6", "7"), ("U5", "3"), ("R9", "1"), ("R24", "1"), ("TP8", "1")},
    "+5V_RLY": {("U5", "5"), ("C16", "1"), ("K1", "1"), ("D4", "1"), ("TP6", "1")},
    "RELAY_SW": {("K1", "8"), ("Q1", "3"), ("D4", "2"), ("TP9", "1")},
    "Q1_B": {("Q1", "1"), ("R9", "2"), ("R10", "1")},
    "K1_NC_FB": {("K1", "7"), ("R25", "2"), ("U1", "4"), ("TP12", "1")},
    "K1_NO_FB": {("K1", "5"), ("R26", "2"), ("U1", "5"), ("TP13", "1")},
    "TX_ENABLE": {("U1", "8"), ("U6", "5"), ("R27", "1")},
    "TX_GATE": {("U6", "3"), ("U7", "1"), ("R28", "1"), ("TP10", "1")},
    "TX_BUF": {("U7", "4"), ("R6", "1")},
    "TX_DRV": {("K1", "4"), ("R6", "2"), ("TP11", "1")},
    "ESP_TX": {("U7", "2"), ("U1", "10")},
    "CONS6": {("J1", "6"), ("K1", "2"), ("R7", "1"), ("D5", "1")},
    "MOT6": {("J2", "6"), ("K1", "3"), ("D6", "1")},
    "VBUS_PRESENT_N": {("Q2", "3"), ("R30", "2"), ("U1", "7")},
}
_NET_LOCKS = MappingProxyType({
    net: frozenset(pads)
    for net, pads in _NET_LOCKS.items()
})

_TERMINAL_NET_LOCKS = {
    "R15": {"USB_DN_MCU", "USB_DN_R"},
    "R16": {"USB_DP_MCU", "USB_DP_R"},
    "R17": {"VIN", "UV_SENSE"},
    "R18": {"UV_SENSE", "GND"},
    "R19": {"VIN", "OV_SENSE"},
    "R20": {"OV_SENSE", "GND"},
    "R21": {"+3V3", "TREAD_OK"},
    "R22": {"TREAD_OK", "GND"},
    "R23": {"RELAY_CMD", "GND"},
    "R24": {"RELAY_GATE", "GND"},
    "R25": {"+3V3", "K1_NC_FB"},
    "R26": {"+3V3", "K1_NO_FB"},
    "R27": {"TX_ENABLE", "GND"},
    "R28": {"TX_GATE", "GND"},
    "R29": {"VBUS", "GND"},
    "R30": {"+3V3", "VBUS_PRESENT_N"},
    "R31": {"+3V3", "IO0"},
    "C12": {"+3V3", "FB"},
    "C13": {"USB_DN_R", "GND"},
    "C14": {"USB_DP_R", "GND"},
    "C15": {"VIN", "GND"},
    "C16": {"+5V_RLY", "GND"},
    "C17": {"VIN", "GND"},
    "C18": {"UV_SENSE", "GND"},
    "C19": {"OV_SENSE", "GND"},
    "C20": {"+3V3", "GND"},
    "C21": {"+3V3", "GND"},
    "U4": {"TREAD_OK", "GND", "UV_SENSE", "OV_SENSE", "VIN"},
    "U5": {"VIN", "GND", "RELAY_GATE", "+5V_RLY", "<NC>"},
    "U6": {"RELAY_CMD", "TREAD_OK", "RELAY_GATE", "TX_ENABLE", "TX_GATE", "GND", "+3V3"},
    "U7": {"TX_GATE", "ESP_TX", "GND", "TX_BUF", "+3V3"},
    "Q2": {"VBUS", "GND", "VBUS_PRESENT_N"},
}
_TERMINAL_NET_LOCKS = MappingProxyType({
    ref: frozenset(nets)
    for ref, nets in _TERMINAL_NET_LOCKS.items()
})

_VBUS_ADJACENT_PIN_NET_LOCKS = MappingProxyType({
    ("J3", "A1"): "GND",
    ("J3", "A4"): "VBUS",
    ("J3", "A5"): "CC1",
    ("J3", "A6"): "USB_DP",
    ("J3", "A7"): "USB_DN",
    ("J3", "A8"): "<NC>",
    ("J3", "A9"): "VBUS",
    ("J3", "A12"): "GND",
    ("J3", "B1"): "GND",
    ("J3", "B4"): "VBUS",
    ("J3", "B5"): "CC2",
    ("J3", "B6"): "USB_DP",
    ("J3", "B7"): "USB_DN",
    ("J3", "B8"): "<NC>",
    ("J3", "B9"): "VBUS",
    ("J3", "B12"): "GND",
    ("J3", "S1"): "GND",
    ("U3", "1"): "USB_DN",
    ("U3", "2"): "GND",
    ("U3", "3"): "USB_DP",
    ("U3", "4"): "USB_DP_MCU",
    ("U3", "5"): "VBUS",
    ("U3", "6"): "USB_DN_MCU",
    ("C11", "1"): "VBUS",
    ("C11", "2"): "GND",
    ("R29", "1"): "VBUS",
    ("R29", "2"): "GND",
    ("Q2", "1"): "VBUS",
    ("Q2", "2"): "GND",
    ("Q2", "3"): "VBUS_PRESENT_N",
})
_VBUS_ADJACENT_REFS = frozenset({
    "J3",
    "U3",
    "C11",
    "R29",
    "Q2",
})

_POWER_PIN_NET_LOCKS = MappingProxyType({
    ("U1", "1"): "GND",
    ("U1", "2"): "+3V3",
    ("U1", "40"): "GND",
    ("U1", "41"): "GND",
    ("U2", "1"): "GND",
    ("U2", "2"): "SW_NODE",
    ("U2", "3"): "VIN",
    ("U2", "4"): "FB",
    ("U2", "5"): "BUCK_EN",
    ("U2", "6"): "BST",
    ("U3", "2"): "GND",
    ("U3", "5"): "VBUS",
    ("U4", "2"): "GND",
    ("U4", "5"): "VIN",
    ("U5", "1"): "VIN",
    ("U5", "2"): "GND",
    ("U5", "4"): "<NC>",
    ("U5", "5"): "+5V_RLY",
    ("U6", "4"): "GND",
    ("U6", "8"): "+3V3",
    ("U7", "3"): "GND",
    ("U7", "5"): "+3V3",
    ("Q2", "1"): "VBUS",
    ("Q2", "2"): "GND",
    ("Q2", "3"): "VBUS_PRESENT_N",
    ("K1", "1"): "+5V_RLY",
    ("K1", "8"): "RELAY_SW",
})

_USB_PIN_NET_LOCKS = MappingProxyType({
    ("J3", "A6"): "USB_DP",
    ("J3", "B6"): "USB_DP",
    ("J3", "A7"): "USB_DN",
    ("J3", "B7"): "USB_DN",
    ("U3", "1"): "USB_DN",
    ("U3", "3"): "USB_DP",
    ("U3", "4"): "USB_DP_MCU",
    ("U3", "6"): "USB_DN_MCU",
})

_SAFETY_PIN_NET_LOCKS = MappingProxyType({
    ("U1", "4"): "K1_NC_FB",
    ("U1", "5"): "K1_NO_FB",
    ("U1", "6"): "TREAD_OK",
    ("U1", "7"): "VBUS_PRESENT_N",
    ("U1", "8"): "TX_ENABLE",
    ("U1", "10"): "ESP_TX",
    ("U1", "23"): "RELAY_CMD",
    ("U4", "1"): "TREAD_OK",
    ("U4", "2"): "GND",
    ("U4", "3"): "UV_SENSE",
    ("U4", "4"): "OV_SENSE",
    ("U4", "5"): "VIN",
    ("U4", "6"): "TREAD_OK",
    ("U5", "1"): "VIN",
    ("U5", "2"): "GND",
    ("U5", "3"): "RELAY_GATE",
    ("U5", "4"): "<NC>",
    ("U5", "5"): "+5V_RLY",
    ("U6", "1"): "RELAY_CMD",
    ("U6", "2"): "TREAD_OK",
    ("U6", "3"): "TX_GATE",
    ("U6", "4"): "GND",
    ("U6", "5"): "TX_ENABLE",
    ("U6", "6"): "TREAD_OK",
    ("U6", "7"): "RELAY_GATE",
    ("U6", "8"): "+3V3",
    ("U7", "1"): "TX_GATE",
    ("U7", "2"): "ESP_TX",
    ("U7", "3"): "GND",
    ("U7", "4"): "TX_BUF",
    ("U7", "5"): "+3V3",
    ("Q1", "1"): "Q1_B",
    ("Q1", "2"): "GND",
    ("Q1", "3"): "RELAY_SW",
    ("K1", "1"): "+5V_RLY",
    ("K1", "2"): "CONS6",
    ("K1", "3"): "MOT6",
    ("K1", "4"): "TX_DRV",
    ("K1", "5"): "K1_NO_FB",
    ("K1", "6"): "GND",
    ("K1", "7"): "K1_NC_FB",
    ("K1", "8"): "RELAY_SW",
    ("D4", "1"): "+5V_RLY",
    ("D4", "2"): "RELAY_SW",
    ("R6", "1"): "TX_BUF",
    ("R6", "2"): "TX_DRV",
    ("R9", "1"): "RELAY_GATE",
    ("R9", "2"): "Q1_B",
    ("R10", "1"): "Q1_B",
    ("R10", "2"): "GND",
})

_EXPECTED_DNP = frozenset({"C13", "C14"})
_ALLOWED_PIN_TYPES = frozenset({
    "power_in",
    "power_out",
    "input",
    "output",
    "open_collector",
    "tri_state",
    "passive",
    "bidirectional",
    "no_connect",
})
_VALUE_LOCKS = MappingProxyType({
    "R7": ("10k", "C25804"),
    "R8": ("10k", "C25804"),
    "C11": ("100nF", "C14663"),
})


class DesignValidationError(ValueError):
    """The tuple-based design violates a locked electrical invariant."""


def require(condition, message):
    """Raise a validation error even when Python assertions are disabled."""
    if not condition:
        raise DesignValidationError(message)


def _sorted_for_message(values):
    return sorted(values, key=repr)


def _require_exact_set(actual, expected, label):
    missing = expected - actual
    extra = actual - expected
    require(
        not missing and not extra,
        f"{label}: missing={_sorted_for_message(missing)}; "
        f"extra={_sorted_for_message(extra)}; "
        f"actual={_sorted_for_message(actual)}; "
        f"expected={_sorted_for_message(expected)}",
    )


def _require_pin_net_locks(used, locks, label):
    for (ref, pad), expected in locks.items():
        actual = used.get((ref, pad), "<missing>")
        require(
            actual == expected,
            f"{label} {ref}.{pad}: actual={actual}; "
            f"expected={expected}",
        )


def _safety_lock_label(ref, pad):
    pin = (ref, pad)
    if ref in {"K1", "Q1", "D4", "R9", "R10", "U5"} or pin in {
        ("U1", "4"),
        ("U1", "5"),
        ("U1", "6"),
        ("U1", "23"),
        ("U4", "1"),
        ("U4", "3"),
        ("U4", "4"),
        ("U4", "6"),
        ("U6", "1"),
        ("U6", "2"),
        ("U6", "7"),
    }:
        return "relay safety lock"
    if pin in {
        ("U1", "8"),
        ("U6", "3"),
        ("U6", "5"),
        ("U6", "6"),
        ("U7", "1"),
    }:
        return "TX gate safety lock"
    if pin in {
        ("U1", "10"),
        ("U7", "2"),
        ("U7", "4"),
        ("R6", "1"),
        ("R6", "2"),
    }:
        return "TX buffer safety lock"
    return "safety topology lock"


def _require_safety_pin_net_locks(used):
    for (ref, pad), expected in _SAFETY_PIN_NET_LOCKS.items():
        actual = used.get((ref, pad), "<missing>")
        label = _safety_lock_label(ref, pad)
        require(
            actual == expected,
            f"{label} {ref}.{pad}: actual={actual}; "
            f"expected={expected}",
        )


def validate():
    component_pins = set()
    for ref, component in COMPONENTS.items():
        require(
            isinstance(component, tuple) and len(component) == 8,
            f"{ref} must use the 8-field component tuple schema",
        )
        pin_map = component[7]
        require(
            isinstance(pin_map, dict) and pin_map,
            f"{ref} has no pad map",
        )
        for pad in pin_map:
            require(
                isinstance(pad, str),
                f"{ref} pad numbers must be strings",
            )
            component_pins.add((ref, pad))

    used = {}
    for net, pads in NETS.items():
        for ref, pad in pads:
            require(ref in COMPONENTS, f"unknown ref {ref}")
            require(
                pad in COMPONENTS[ref][7],
                f"{ref}.{pad} not a defined pin",
            )
            key = (ref, pad)
            require(
                key not in used,
                f"{ref}.{pad} in both {used.get(key)} and {net}",
            )
            used[key] = net
    for ref, pad in NC:
        require(ref in COMPONENTS, f"unknown NC ref {ref}")
        require(
            pad in COMPONENTS[ref][7],
            f"NC pin {ref}.{pad} is not defined",
        )
        key = (ref, pad)
        require(
            key not in used,
            f"NC pin {ref}.{pad} also in net {used.get(key)}",
        )
        used[key] = "<NC>"
    missing = [
        f"{ref}.{pad}"
        for ref, pad in sorted(component_pins - used.keys())
    ]
    require(
        not missing,
        f"pins with no net and no NC: {missing}",
    )

    # every net must have at least 2 pins
    for net, pads in NETS.items():
        require(len(pads) >= 2, f"net {net} has <2 pins")

    pin_type_pins = set(PIN_TYPES)
    pin_type_missing = component_pins - pin_type_pins
    pin_type_extra = pin_type_pins - component_pins
    require(
        not pin_type_missing and not pin_type_extra,
        "PIN_TYPES table mismatch: "
        f"missing={_sorted_for_message(pin_type_missing)}; "
        f"extra={_sorted_for_message(pin_type_extra)}",
    )
    unknown_pin_types = {
        pin_type
        for pin_type in PIN_TYPES.values()
        if pin_type not in _ALLOWED_PIN_TYPES
    }
    require(
        not unknown_pin_types,
        "PIN_TYPES unknown KiCad types: "
        f"{_sorted_for_message(unknown_pin_types)}",
    )
    expected_pin_types = {
        pin: _PIN_TYPE_OVERRIDES.get(pin, "passive")
        for pin in component_pins
    }
    derived_mismatches = [
        f"{ref}.{pad}: actual={PIN_TYPES[(ref, pad)]}; "
        f"expected={expected_pin_types[(ref, pad)]}"
        for ref, pad in sorted(component_pins)
        if PIN_TYPES[(ref, pad)] != expected_pin_types[(ref, pad)]
    ]
    require(
        not derived_mismatches,
        "PIN_TYPES derived table mismatch: "
        f"{derived_mismatches}",
    )

    for (ref, pad), expected_type in _ACTIVE_PIN_TYPE_LOCKS.items():
        actual_type = PIN_TYPES.get((ref, pad), "<missing>")
        require(
            actual_type == expected_type,
            f"PIN_TYPES active lock {ref}.{pad}: "
            f"actual={actual_type}; expected={expected_type}",
        )
    actual_nonpassive = {
        pin: pin_type
        for pin, pin_type in PIN_TYPES.items()
        if pin_type != "passive"
    }
    unexpected_nonpassive = (
        actual_nonpassive.keys() - _ACTIVE_PIN_TYPE_LOCKS.keys()
    )
    require(
        not unexpected_nonpassive,
        "PIN_TYPES active lock has unexpected non-passive pins: "
        f"extra={_sorted_for_message(unexpected_nonpassive)}",
    )
    actual_no_connect = {
        pin
        for pin, pin_type in PIN_TYPES.items()
        if pin_type == "no_connect"
    }
    _require_exact_set(
        actual_no_connect,
        set(NC),
        "PIN_TYPES no_connect lock",
    )

    actual_dnp = set(DNP)
    _require_exact_set(
        actual_dnp,
        _EXPECTED_DNP,
        "DNP set mismatch",
    )
    for ref in _EXPECTED_DNP:
        require(ref in COMPONENTS, f"unknown DNP ref {ref}")
        component = COMPONENTS[ref]
        require(component[0] == "DNP", f"DNP {ref} must have value DNP")
        require(
            component[3] == ""
            and component[4] == "DNP"
            and component[5] == 0,
            f"DNP {ref} is populated: LCSC={component[3]!r}; "
            f"class={component[4]!r}; cost={component[5]!r}",
        )
    dnp_valued = {
        ref
        for ref, component in COMPONENTS.items()
        if component[0] == "DNP"
    }
    _require_exact_set(
        dnp_valued,
        _EXPECTED_DNP,
        "DNP-valued component mismatch",
    )
    dnp_metadata = {
        ref
        for ref, component in COMPONENTS.items()
        if component[4] == "DNP"
    }
    _require_exact_set(
        dnp_metadata,
        _EXPECTED_DNP,
        "DNP assembly metadata mismatch",
    )

    require(
        "D2" not in COMPONENTS,
        "USB-to-VIN diode D2 is forbidden",
    )
    for ref, expected in _PART_LOCKS.items():
        component = COMPONENTS[ref]
        actual = (
            component[0],
            component[1],
            component[2],
            component[3],
            tuple(sorted(component[7].items())),
        )
        require(
            actual == expected,
            f"{ref} part/package/pad lock mismatch: "
            f"actual={actual}; expected={expected}",
        )
    for ref, expected in _VALUE_LOCKS.items():
        actual = (COMPONENTS[ref][0], COMPONENTS[ref][3])
        require(
            actual == expected,
            f"{ref} value lock mismatch: "
            f"actual={actual}; expected={expected}",
        )

    _require_pin_net_locks(
        used,
        _POWER_PIN_NET_LOCKS,
        "power-pad lock",
    )
    _require_pin_net_locks(
        used,
        _USB_PIN_NET_LOCKS,
        "USB polarity lock",
    )
    vbus_adjacent_component_pins = {
        (ref, pad)
        for ref in _VBUS_ADJACENT_REFS
        for pad in COMPONENTS[ref][7]
    }
    _require_exact_set(
        set(_VBUS_ADJACENT_PIN_NET_LOCKS),
        vbus_adjacent_component_pins,
        "VBUS isolation lock coverage",
    )
    _require_pin_net_locks(
        used,
        _VBUS_ADJACENT_PIN_NET_LOCKS,
        "VBUS isolation lock",
    )
    _require_safety_pin_net_locks(used)

    for net, expected_pads in _NET_LOCKS.items():
        require(net in NETS, f"required Rev B net {net} is missing")
        _require_exact_set(
            set(NETS[net]),
            expected_pads,
            f"net topology lock {net}",
        )
    for ref, expected_nets in _TERMINAL_NET_LOCKS.items():
        actual_nets = {
            used[(ref, pad)]
            for pad in COMPONENTS[ref][7]
        }
        _require_exact_set(
            actual_nets,
            expected_nets,
            f"terminal-net lock {ref}",
        )

    # No component touched by VBUS may also touch a board power rail.  This
    # explicitly preserves data-only USB even if a passive endpoint is moved.
    board_power_rails = {"+8V_RAW", "+8V_F", "VIN", "+3V3", "+5V_RLY"}
    vbus_refs = {ref for ref, _pad in NETS["VBUS"]}
    for ref in vbus_refs:
        terminal_nets = {
            used[(ref, pad)]
            for pad in COMPONENTS[ref][7]
            if used[(ref, pad)] != "<NC>"
        }
        forbidden_rails = terminal_nets & board_power_rails
        require(
            not forbidden_rails,
            f"VBUS power path through {ref}: "
            f"forbidden rails={_sorted_for_message(forbidden_rails)}; "
            f"terminal nets={_sorted_for_message(terminal_nets)}",
        )

    require(
        used[("U5", "4")] == "<NC>",
        "U5.4 must remain deliberately unconnected",
    )
    actual_gpio_map = {
        pad: used[("U1", pad)]
        for pad in ("4", "5", "6", "7", "8", "9", "10", "11", "23", "27", "31")
    }
    expected_gpio_map = {
        "4": "K1_NC_FB",
        "5": "K1_NO_FB",
        "6": "TREAD_OK",
        "7": "VBUS_PRESENT_N",
        "8": "TX_ENABLE",
        "9": "PIN3_RX",
        "10": "ESP_TX",
        "11": "CONS_RX",
        "23": "RELAY_CMD",
        "27": "IO0",
        "31": "STATUS_LED",
    }
    require(
        actual_gpio_map == expected_gpio_map,
        "U1 GPIO map mismatch: "
        f"actual={actual_gpio_map}; expected={expected_gpio_map}",
    )
    rj45_ground_nets = {
        used[(connector, pad)]
        for connector in ("J1", "J2")
        for pad in ("1", "7")
    }
    require(
        rj45_ground_nets == {"GND"},
        "RJ45 ground pass-through mismatch: "
        f"actual={rj45_ground_nets}; expected={{'GND'}}",
    )
    actual_u6_equations = {
        used[("U6", "7")]: frozenset({used[("U6", "1")], used[("U6", "2")]}),
        used[("U6", "3")]: frozenset({used[("U6", "5")], used[("U6", "6")]}),
    }
    expected_u6_equations = {
        "RELAY_GATE": frozenset({"RELAY_CMD", "TREAD_OK"}),
        "TX_GATE": frozenset({"TX_ENABLE", "TREAD_OK"}),
    }
    require(
        actual_u6_equations == expected_u6_equations,
        "U6 gate equation mismatch: "
        f"actual={actual_u6_equations}; "
        f"expected={expected_u6_equations}",
    )
    actual_d4_nets = {
        used[("D4", "1")],
        used[("D4", "2")],
    }
    expected_d4_nets = {
        used[("K1", "1")],
        used[("K1", "8")],
    }
    require(
        actual_d4_nets == expected_d4_nets,
        "D4 coil-clamp mismatch: "
        f"actual={actual_d4_nets}; expected={expected_d4_nets}",
    )
    actual_k1_contacts = {
        pad: used[("K1", pad)]
        for pad in ("2", "3", "4", "5", "6", "7")
    }
    expected_k1_contacts = {
        "2": "CONS6",
        "3": "MOT6",
        "4": "TX_DRV",
        "5": "K1_NO_FB",
        "6": "GND",
        "7": "K1_NC_FB",
    }
    require(
        actual_k1_contacts == expected_k1_contacts,
        "K1 fail-safe contact allocation mismatch: "
        f"actual={actual_k1_contacts}; "
        f"expected={expected_k1_contacts}",
    )
    actual_u7_map = {
        pad: used[("U7", pad)]
        for pad in ("1", "2", "3", "4", "5")
    }
    expected_u7_map = {
        "1": "TX_GATE",
        "2": "ESP_TX",
        "3": "GND",
        "4": "TX_BUF",
        "5": "+3V3",
    }
    require(
        actual_u7_map == expected_u7_map,
        "U7 transmit-isolation mismatch: "
        f"actual={actual_u7_map}; expected={expected_u7_map}",
    )
    return True


if __name__ == "__main__":
    validate()
    npins = sum(len(c[7]) for c in COMPONENTS.values())
    print(f"OK: {len(COMPONENTS)} components, {npins} pins, " f"{len(NETS)} nets, {len(NC)} no-connects")

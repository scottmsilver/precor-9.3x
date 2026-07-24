* Esp32Tap Rev B shared ngspice behavioral primitives
*
* These are deliberately transparent envelope models, not vendor macromodels.
* Selected-part anchors:
* - F1 1812L075/24DR: 0.11 ohm initial minimum and 0.29 ohm
*   post-trip maximum are represented only as fixed resistance corners.
*   PTC thermal trip, hold, reset, and pulse heating are not modeled.
* - D1 SS34: low-order Schottky diode approximation.
* - D3 SMBJ10A: 10 V standoff; separate 11.1 V minimum-breakdown and
*   12.3 V maximum-breakdown/high-clamp approximations bound current/energy
*   and protected VIN respectively.
* - D4 SMAJ6.0CA: 6 V standoff; 6.67...7.37 V breakdown and
*   10.3 V at 38.8 A are bounded with a piecewise behavioral clamp.
* - K1 G6K-2F-Y-TR DC5: 5 V, 237 ohm nominal coil.  Contact motion is
*   not modeled; inductance is explicitly swept because Omron does not
*   publish it as a guaranteed parameter.
* - Q1 BC817-40,215: dynamic release branches use an ideal saturated switch;
*   a deck-level diagnostic applies conservative drive and the published
*   0.7 V VCEsat value only with the specified forced-beta-10 condition.
* - TPS3700: 396...404 mV rising threshold, 387...400 mV falling
*   threshold and 450 us maximum startup delay are deck-level corners.
* - TPS70950: its 1.5 ms maximum EN-to-95%-VOUT startup is represented by
*   an explicit deck-level ramp and enabled 5 V Thevenin source; regulator
*   loop, current limit, and thermal behavior are not modeled.
* - 2N7002: represented as a voltage-controlled open-drain switch with
*   1.0...2.5 V threshold corners; gate leakage/capacitance are not modeled.
*
* Sources: manufacturer data sheets cited in ../README.md.

.model ESP_SS34 D(Is=5u N=1.06 Rs=0.045 Cjo=250p Tt=10n Bv=40 Ibv=1m)
.model ESP_SMBJ10A D(Is=1n N=1.1 Rs=0.25 Cjo=1n Bv=11.1 Ibv=1m)
.model ESP_SMBJ10A_HIGH D(Is=1n N=1.1 Rs=0.124 Cjo=1n Bv=12.3 Ibv=1m)
.model ESP_SIGNAL_DIODE D(Is=2n N=1.7 Rs=0.2 Cjo=5p)
.model ESP_GPIO_CLAMP D(Is=1n N=1.35 Rs=1 Cjo=5p)
.model ESP_SWITCH SW(Ron=0.05 Roff=1e12 Vt=1.65 Vh=0.05)
.model ESP_SWITCH_1V SW(Ron=5 Roff=1e12 Vt=1.0 Vh=0.02)
.model ESP_SWITCH_2V5 SW(Ron=5 Roff=1e12 Vt=2.5 Vh=0.02)

* Symmetric fast-release clamp.  VCLAMP is the low-current avalanche
* corner and RDYN raises the clamp toward the data-sheet high-current value.
.subckt ESP_BIDI_TVS p n params: VCLAMP=6.67 RDYN=0.094
BCLAMP p n I={u(v(p,n)-VCLAMP)*(v(p,n)-VCLAMP)/RDYN+u(-v(p,n)-VCLAMP)*(v(p,n)+VCLAMP)/RDYN}
RCLAMP_LEAK p n 1g
.ends ESP_BIDI_TVS

* Partial-power-down AND behavior used for SN74LVC2G08 truth-table work.
* The output is forced to zero when the modeled +3V3 rail is absent.
.subckt ESP_AND_GATE a b rail y
BY 0 y I={(v(rail)*u(v(rail)-1.65)*u(v(a)-1.65)*u(v(b)-1.65)-v(y))/5}
RY y 0 1g
.ends ESP_AND_GATE

* Active-low 2N7002 VBUS detector.  Gate and drain are intentionally
* separate: the model has no conductive VBUS-to-GPIO/+3V3 path.
.subckt ESP_VBUS_DETECTOR gate rail out params: CORNER=1
RPULL rail out 10k
SDET out 0 gate 0 ESP_SWITCH_1V
.ends ESP_VBUS_DETECTOR

.subckt ESP_VBUS_DETECTOR_2V5 gate rail out
RPULL rail out 10k
SDET out 0 gate 0 ESP_SWITCH_2V5
.ends ESP_VBUS_DETECTOR_2V5

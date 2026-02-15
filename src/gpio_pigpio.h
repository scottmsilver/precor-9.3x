/*
 * gpio_pigpio.h — PigpioPort: thin inline wrappers around libpigpio
 *
 * Zero overhead — every method is a direct call to the pigpio C API.
 * Only included in the production binary (links libpigpio).
 */

#pragma once

#include <pigpio.h>
#include "gpio_port.h"

struct PigpioPort {
    int initialise() { return gpioInitialise() < 0 ? -1 : 0; }
    void terminate() { gpioTerminate(); }

    void set_mode(int pin, int mode) {
        gpioSetMode(pin, mode == PORT_OUTPUT ? PI_OUTPUT : PI_INPUT);
    }

    void write(int pin, int level) { gpioWrite(pin, level); }

    int serial_read_open(int pin, int baud, int bits) {
        return gpioSerialReadOpen(pin, baud, bits);
    }

    void serial_read_invert(int pin, int invert) {
        gpioSerialReadInvert(pin, invert);
    }

    int serial_read(int pin, void* buf, int bufsize) {
        return gpioSerialRead(pin, buf, bufsize);
    }

    void serial_read_close(int pin) {
        gpioSerialReadClose(pin);
    }

    int wave_tx_busy() { return gpioWaveTxBusy(); }
    void wave_clear() { gpioWaveClear(); }

    void wave_add_generic(int num_pulses, gpioPulse_t* pulses) {
        gpioWaveAddGeneric(num_pulses, pulses);
    }

    int wave_create() { return gpioWaveCreate(); }

    void wave_tx_send(int wid, int mode) {
        gpioWaveTxSend(wid, mode == PORT_WAVE_MODE_ONE_SHOT
                            ? PI_WAVE_MODE_ONE_SHOT : PI_WAVE_MODE_ONE_SHOT);
    }

    void wave_delete(int wid) { gpioWaveDelete(wid); }
};

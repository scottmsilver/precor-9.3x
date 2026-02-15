/*
 * gpio_port.h — GpioPort compile-time interface documentation
 *
 * Any type used as a Port template parameter must provide these methods.
 * This isn't enforced by a C++ concept (C++20), but serves as documentation.
 * PigpioPort and MockGpioPort both satisfy this interface.
 *
 * Required interface:
 *
 *   int  initialise();                    // Init GPIO system. Returns 0 on success.
 *   void terminate();                     // Shutdown GPIO system.
 *   void set_mode(int pin, int mode);     // PI_OUTPUT or PI_INPUT
 *   void write(int pin, int level);       // 0 or 1
 *   int  serial_read_open(int pin, int baud, int bits);
 *   void serial_read_invert(int pin, int invert);
 *   int  serial_read(int pin, void* buf, int bufsize);
 *   void serial_read_close(int pin);
 *   int  wave_tx_busy();
 *   void wave_clear();
 *   void wave_add_generic(int num_pulses, gpioPulse_t* pulses);
 *   int  wave_create();
 *   void wave_tx_send(int wid, int mode);
 *   void wave_delete(int wid);
 *
 * gpioPulse_t struct (from pigpio.h or defined by mock):
 *   uint32_t gpioOn;
 *   uint32_t gpioOff;
 *   uint32_t usDelay;
 */

#pragma once

// Mode constants (match pigpio)
constexpr int PORT_INPUT  = 0;
constexpr int PORT_OUTPUT = 1;

// Wave mode constants
constexpr int PORT_WAVE_MODE_ONE_SHOT = 0;

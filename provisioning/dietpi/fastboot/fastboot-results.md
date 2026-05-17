# Fast-Boot Results Log (rpi-zero, Pi Zero 2 W, DietPi 10.3)
KPI = Pi-side /proc/uptime at first SSH key-auth (3-cycle mean). Lower = better.
Decision rule: keep if mean improves >=0.5s vs prior kept state & no SSH regression.

| Layer | mean (s) | min | max | kept? | notes |
|-------|----------|-----|-----|-------|-------|
## measure: baseline  (2026-05-17T15:10:18Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=12.99s
  cycle 2: boot-side-uptime-at-ssh=15.96s
  cycle 3: boot-side-uptime-at-ssh=13.26s
  RESULT baseline: mean=14.1s min=12.99s max=15.96s n=3
  Startup finished in 3.106s (kernel) + 9.646s (userspace) = 12.752s
  graphical.target reached after 9.576s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +325ms
  └─network.target @9.216s
    └─ifup@wlan0.service @3.835s +5.381s
      └─network-pre.target @3.803s
        └─dietpi-preboot.service @3.570s +232ms
  --pathA--
  -- No entries --
## measure: L0-decouple  (2026-05-17T15:13:48Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=16.59s
  cycle 2: boot-side-uptime-at-ssh=14.36s
  cycle 3: boot-side-uptime-at-ssh=15.59s
  RESULT L0-decouple: mean=15.5s min=14.36s max=16.59s n=3
  Startup finished in 3.216s (kernel) + 11.895s (userspace) = 15.112s
  graphical.target reached after 11.825s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +328ms
  └─network.target @11.467s
    └─ifup@wlan0.service @3.884s +7.582s
      └─network-pre.target @3.856s
        └─dietpi-preboot.service @3.635s +220ms
  --pathA--
  May 17 15:15:47 rpi-zero sh[234]: pathA-fired uptime=6.69
  May 17 15:15:47 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: baseline-bt  (2026-05-17T17:40:19Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=8715.52s
  cycle 2: boot-side-uptime-at-ssh=15.49s
  cycle 3: boot-side-uptime-at-ssh=13.33s
  RESULT baseline-bt: mean=2914.8s min=13.33s max=8715.52s n=3
  Startup finished in 3.208s (kernel) + 8.299s (userspace) = 11.508s
  graphical.target reached after 8.233s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +301ms
  └─network.target @7.897s
    └─ifup@wlan0.service @4.138s +3.759s
      └─network-pre.target @4.110s
        └─dietpi-preboot.service @3.787s +322ms
  --pathA--
  May 17 17:41:48 rpi-zero sh[242]: pathA-fired uptime=6.74
  May 17 17:41:48 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: baseline-bt  (2026-05-17T17:43:49Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=16.98s (pre=108.04s)
  cycle 2: boot-side-uptime-at-ssh=15.49s (pre=22.51s)
  cycle 3: boot-side-uptime-at-ssh=12.89s (pre=20.99s)
  RESULT baseline-bt: mean=15.1s min=12.89s max=16.98s valid=3 bad=0
  Startup finished in 3.192s (kernel) + 9.153s (userspace) = 12.345s
  graphical.target reached after 9.097s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +325ms
  └─network.target @8.737s
    └─ifup@wlan0.service @4.092s +4.644s
      └─network-pre.target @4.070s
        └─dietpi-preboot.service @3.752s +317ms
  --pathA--
  May 17 17:44:36 rpi-zero sh[242]: pathA-fired uptime=6.71
  May 17 17:44:36 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1-cached-lease  (2026-05-17T17:50:16Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=9.47s (pre=69.37s)
  cycle 2: boot-side-uptime-at-ssh=10.78s (pre=14.88s)
  cycle 3: boot-side-uptime-at-ssh=9.49s (pre=16.24s)
  RESULT L1-cached-lease: mean=9.9s min=9.47s max=10.78s valid=3 bad=0
  Startup finished in 3.220s (kernel) + 5.288s (userspace) = 8.509s
  graphical.target reached after 5.213s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +313ms
  └─network.target @4.862s
    └─ifup@wlan0.service @4.167s +694ms
      └─network-pre.target @4.155s
        └─dietpi-preboot.service @3.755s +397ms
  --pathA--
  May 17 17:50:46 rpi-zero sh[239]: pathA-fired uptime=6.74
  May 17 17:50:46 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1-selfrecover  (2026-05-17T17:51:27Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=15.17s (pre=18.29s)
  cycle 2: boot-side-uptime-at-ssh=17.41s (pre=20.60s)
  cycle 3: boot-side-uptime-at-ssh=12.92s (pre=22.87s)
  RESULT L1-selfrecover: mean=15.2s min=12.92s max=17.41s valid=3 bad=0
  Startup finished in 3.145s (kernel) + 5.250s (userspace) = 8.395s
  graphical.target reached after 5.189s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.

  ssh.service +309ms
  └─network.target @4.854s
    └─ifup@wlan0.service @4.181s +672ms
      └─network-pre.target @4.169s
        └─dietpi-preboot.service @3.727s +440ms
  --pathA--
  May 17 17:51:39 rpi-zero sh[244]: pathA-fired uptime=6.66
  May 17 17:51:39 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L2-bssid-pin  (2026-05-17T17:55:18Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=13.01s (pre=166.72s)
  cycle 2: boot-side-uptime-at-ssh=14.02s (pre=18.43s)
  cycle 3: boot-side-uptime-at-ssh=15.21s (pre=19.52s)
  RESULT L2-bssid-pin: mean=14.1s min=13.01s max=15.21s valid=3 bad=0
  Startup finished in 3.137s (kernel) + 5.302s (userspace) = 8.439s 
  graphical.target reached after 5.217s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +317ms
  └─network.target @4.865s
    └─ifup@wlan0.service @4.193s +671ms
      └─network-pre.target @4.182s
        └─dietpi-preboot.service @3.759s +421ms
  --pathA--
  May 17 17:55:55 rpi-zero sh[240]: pathA-fired uptime=6.67
  May 17 17:55:55 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L2-fallback  (2026-05-17T17:56:41Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=15.37s (pre=24.16s)
  cycle 2: boot-side-uptime-at-ssh=17.41s (pre=20.87s)
  cycle 3: boot-side-uptime-at-ssh=15.24s (pre=22.95s)
  RESULT L2-fallback: mean=16.0s min=15.24s max=17.41s valid=3 bad=0
  Startup finished in 3.143s (kernel) + 5.286s (userspace) = 8.430s 
  graphical.target reached after 5.210s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +317ms
  └─network.target @4.871s
    └─ifup@wlan0.service @4.185s +684ms
      └─network-pre.target @4.175s
        └─dietpi-preboot.service @3.749s +425ms
  --pathA--
  May 17 17:56:55 rpi-zero sh[240]: pathA-fired uptime=6.65
  May 17 17:56:55 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1-final-clean  (2026-05-17T18:21:47Z)  host=192.168.1.206 cycles=3
  cycle 1 attempt 1: PRE-uptime unreachable
  cycle 1 attempt 2: PRE-uptime unreachable
  cycle 1: FAILED (excluded)
  cycle 2 attempt 1: PRE-uptime unreachable
  cycle 2 attempt 2: PRE-uptime unreachable
  cycle 2: FAILED (excluded)
  cycle 3 attempt 1: PRE-uptime unreachable
  cycle 3 attempt 2: PRE-uptime unreachable
  cycle 3: FAILED (excluded)
  RESULT L1-final-clean: NO VALID CYCLES (bad=3) — measurement inconclusive
## measure: L1b-hardened  (2026-05-17T18:52:09Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=9.72s (pre=89.86s)
  cycle 2: boot-side-uptime-at-ssh=9.76s (pre=15.17s)
  cycle 3: boot-side-uptime-at-ssh=9.78s (pre=15.15s)
  RESULT L1b-hardened: mean=9.8s min=9.72s max=9.78s valid=3 bad=0
  Startup finished in 3.143s (kernel) + 6.189s (userspace) = 9.333s 
  graphical.target reached after 6.122s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +369ms
  └─network.target @5.700s
    └─fastboot-net.service @3.544s +2.155s
      └─sysinit.target @3.513s
        └─systemd-timesyncd.service @3.176s +323ms
  --pathA--
  May 17 18:52:38 rpi-zero sh[243]: pathA-fired uptime=6.70
  May 17 18:52:38 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1b-selfrecover  (2026-05-17T18:53:23Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=10.66s (pre=22.95s)
  cycle 2: boot-side-uptime-at-ssh=9.82s (pre=16.10s)
  cycle 3: boot-side-uptime-at-ssh=9.82s (pre=15.28s)
  RESULT L1b-selfrecover: mean=10.1s min=9.82s max=10.66s valid=3 bad=0
  Startup finished in 3.208s (kernel) + 6.099s (userspace) = 9.308s 
  graphical.target reached after 6.030s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +386ms
  └─network.target @5.617s
    └─fastboot-net.service @3.504s +2.112s
      └─sysinit.target @3.479s
        └─systemd-timesyncd.service @3.164s +314ms
  --pathA--
  May 17 18:53:23 rpi-zero sh[244]: pathA-fired uptime=6.73
  May 17 18:53:23 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1-hardened-final  (2026-05-17T19:15:17Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=15.23s (pre=937.00s)
  cycle 2: boot-side-uptime-at-ssh=12.96s (pre=20.67s)
  cycle 3: boot-side-uptime-at-ssh=16.65s (pre=18.71s)
  RESULT L1-hardened-final: mean=14.9s min=12.96s max=16.65s valid=3 bad=0
  Startup finished in 3.201s (kernel) + 12.814s (userspace) = 16.016s 
  graphical.target reached after 12.748s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +312ms
  └─network.target @12.405s
    └─fastboot-net.service @3.507s +8.898s
      └─sysinit.target @3.475s
        └─systemd-timesyncd.service @3.137s +337ms
  --pathA--
  May 17 19:15:55 rpi-zero sh[243]: pathA-fired uptime=6.73
  May 17 19:15:55 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).
## measure: L1-nonblock-final  (2026-05-17T19:18:51Z)  host=192.168.1.206 cycles=3
  cycle 1: boot-side-uptime-at-ssh=15.19s (pre=153.36s)
  cycle 2: boot-side-uptime-at-ssh=12.95s (pre=20.62s)
  cycle 3: boot-side-uptime-at-ssh=11.67s (pre=18.39s)
  RESULT L1-nonblock-final: mean=13.3s min=11.67s max=15.19s valid=3 bad=0
  Startup finished in 3.127s (kernel) + 7.588s (userspace) = 10.716s 
  graphical.target reached after 7.511s in userspace.
  --chain--
  The time when unit became active or started is printed after the "@" character.
  The time the unit took to start is printed after the "+" character.
  
  ssh.service +309ms
  └─network.target @7.175s
    └─fastboot-net.service @3.484s +3.690s
      └─sysinit.target @3.448s
        └─systemd-timesyncd.service @3.096s +351ms
  --pathA--
  May 17 19:19:29 rpi-zero sh[243]: pathA-fired uptime=6.63
  May 17 19:19:29 rpi-zero systemd[1]: Finished fastboot-probe.service - Fast-boot Path A probe (timestamps the early slot).

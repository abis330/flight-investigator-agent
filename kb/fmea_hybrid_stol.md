# FMEA summary - hybrid-electric STOL (LAT-STOL-H1)

## Propulsion: single DEP motor / ESC loss
Failure mode: one of four distributed electric propulsion motors stops (ESC desync, winding short, connector).
Effects: 25% thrust loss, asymmetric thrust -> yaw transient and steady roll trim offset, remaining motors and
throttle increase, reduced climb gradient. Detection: motorN_rpm drops to ~0 while throttle is high; ESC
telemetry timeout message. Severity: critical in takeoff/climb, major in cruise.

## Power: range-extender generator dropout
Failure mode: generator / ECU shuts down (fuel starvation, coupling failure, ECU fault).
Effects: gen_kw goes to 0 during climb/cruise, battery current rises to carry the full load, state of charge
falls faster, battery temperature rises. Detection: gen_kw = 0 while airborne in climb/cruise with
throttle > 0.2; battery current step up. Severity: major (endurance reduced to battery reserve).

## Power: battery degradation / weak cell group
Failure mode: increased internal resistance and open-circuit-voltage depression (capacity fade, weak cell group).
Effects: pack voltage lower than fleet norm for the same state of charge and current, larger voltage sag
under load, extra heating. Detection: batt_v below fleet envelope while batt_a is normal; batt_temp_c high.
Severity: minor to major depending on remaining margin to low-voltage cutoff.

## Navigation: GNSS jamming (denial)
Failure mode: RF interference removes satellite tracking.
Effects: gnss_nsat drops to 0-3, fix type 0, hdop 99, estimator falls back to dead reckoning so ekf_hacc_m
grows linearly; innovations unavailable. Detection: fix lost message, nsat collapse. Severity: major.

## Navigation: GNSS spoofing
Failure mode: counterfeit GNSS signals slowly drag the position solution.
Effects: satellites and hdop look healthy (often MORE satellites), but EKF GNSS position and velocity
innovations grow steadily until the GPS test ratio gate rejects GNSS. Detection: rising ekf_pos_innov_m with
normal nsat/hdop; consistency between inertial and GNSS diverges. Severity: major / hazardous because it is
silent until the gate trips.

## Sensors: pitot blockage
Failure mode: ice, insect nest or water blocks the pitot tube (drain open -> indicated airspeed decays to 0).
Effects: airspeed_mps decays while groundspeed stays normal; autopilot believes it is slow so it adds
throttle and pitches down, aircraft accelerates and loses altitude until the airspeed monitor switches to
synthetic airspeed. Detection: airspeed minus groundspeed residual far outside wind envelope.
Severity: major.

## Sensors: IMU vibration
Failure mode: propeller imbalance, loose motor mount or degraded IMU isolation.
Effects: vibe_mps2 and accelerometer noise rise 3-7x, gyro noise rises; risk of estimator divergence.
Detection: vibration metric above fleet envelope at the same throttle. Severity: minor to major.

## Flight controls: elevator actuator jam
Failure mode: servo stall, linkage binding or hinge damage freezes the elevator.
Effects: elevator_pos_deg constant while elevator_cmd_deg keeps moving (tracking error), pitch-rate and
altitude oscillation (phugoid) as autopilot falls back to thrust-based pitch control. Detection: elevator
command/position mismatch > 4 deg for > 3 s. Severity: critical.

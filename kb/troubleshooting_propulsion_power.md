# Troubleshooting guide - DEP propulsion and hybrid power

## Reading motor telemetry
The four motors share one collective throttle. In healthy flight all four rpm values agree within ~5%
(max_motor_rpm_imbalance < 0.1). A motor at ~0 rpm while throttle > 0.2 indicates a stopped motor or ESC.
After a motor loss the controller raises throttle; check the other three motors for over-temperature.

## Power balance
Electrical load is supplied by the generator (climb/cruise) and battery. Expect batt_a near 0 to slightly
negative in cruise when the generator follows the load, and large positive current in takeoff and climb.
If gen_kw is 0 during cruise and batt_a is 100+ A, the range extender is offline.

## Battery health indicators
For the same state of charge and current, voltage below fleet norm by > 8 V suggests degradation.
Rising temperature with normal current suggests increased internal resistance.

## Actions
- Motor/ESC: pull ESC fault log, inspect connectors, run-up test before next flight.
- Generator: pull ECU codes, inspect coupling and fuel supply, verify battery reserve logic.
- Battery: impedance test, per-cell voltage comparison, reduce depth of discharge until cleared.

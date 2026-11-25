# Troubleshooting guide - air data and flight controls

## Airspeed sanity check
Indicated airspeed and GNSS ground speed differ by the wind component; with winds up to 10 m/s the residual
should stay within about +/-12 m/s and change slowly. A residual that grows to 20-40 m/s with normal ground
speed means the airspeed sensor is wrong (pitot blockage, failed transducer) - not that the aircraft is slow.
Secondary symptoms of a blocked pitot: throttle increases, aircraft pitches down and loses altitude.

## Elevator tracking
Elevator position should follow command within ~0.5 deg RMS. A constant position while the command moves
(tracking error > 4 deg) means a jammed actuator or linkage. Expect pitch-rate and vertical-speed
oscillations afterwards.

## Vibration
Vibration metric scales with throttle (about 0.4 + 1.1*throttle m/s^2 when healthy). Values several times
higher at the same throttle point to propeller imbalance or mount problems.

## Actions
- Pitot: inspect lines and drains, verify pitot heat, confirm synthetic-airspeed switchover time.
- Elevator: ground the aircraft, inspect servo, linkage and hinge, pull servo current logs.
- Vibration: balance propellers, inspect mounts and IMU isolation, repeat vibration survey.

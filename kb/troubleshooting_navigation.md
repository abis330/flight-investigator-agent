# Troubleshooting guide - GNSS and estimator

## Distinguishing jamming from spoofing
Jamming: satellites disappear (nsat 0-3), fix type 0, hdop very large, EKF position accuracy estimate
(eph / ekf_hacc_m) grows because the filter is dead reckoning. Innovations are not computed.
Spoofing: satellites stay visible and hdop stays good, sometimes nsat even increases. The tell-tale is the
EKF GNSS innovation: position innovation grows steadily (metres per second of drift) together with velocity
innovation, until the innovation test ratio exceeds the gate and GNSS is rejected.

## Normal values
Healthy flight: nsat 12-18, hdop 0.7-0.9, position innovation < 4 m, velocity innovation < 0.5 m/s,
ekf_hacc_m about 1-2 m.

## Actions after a GNSS event
1. Check NOTAMs and local interference reports for the test range.
2. Quantify dead-reckoning drift during the outage and time to reacquire.
3. For suspected spoofing report to range safety and review innovation gate thresholds.
4. Consider CRPA antennas or vision / radio navigation aiding for GNSS-denied operations.

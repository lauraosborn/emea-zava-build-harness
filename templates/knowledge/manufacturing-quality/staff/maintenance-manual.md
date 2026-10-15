# Offline maintenance manual

## Tool Station 4 calibration check
1. Press line pause and confirm the cell is isolated before opening the guard.
2. Fit the certified calibration coupon for the A17 upper mount bracket.
3. Run the dry calibration cycle and verify spindle vibration is below 3.5 mm/s.
4. Check commanded torque against measured torque. The allowed drift is +/-3%.
5. Inspect the tool tip for wear flats, built-up edge, scoring, or debris.
6. If drift is above 3% or vibration is above 3.5 mm/s, replace the tool, clean the mount, and rerun calibration.
7. Record the calibration result, operator ID, component batch, and any held serial numbers in the quality log.

## Surface finish containment
When inconsistent surface finish is found near a mounting point, quarantine the current part and the previous ten parts from the same station. Photograph the defect locally, record station, tool ID, batch and telemetry, then run Tool Station 4 calibration before releasing production.

## Offline inspection mode
If the network is disconnected, continue inspection using local reference images, the offline maintenance manual, local telemetry export, and the on-device model. Do not upload component images or fault logs. Synchronise the quality log only after network recovery and supervisor approval.

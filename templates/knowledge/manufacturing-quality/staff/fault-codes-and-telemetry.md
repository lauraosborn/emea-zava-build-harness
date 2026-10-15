# Fault codes and telemetry

## QI-217 surface finish drift
QI-217 means the quality camera and station telemetry both indicate surface finish drift. Common evidence: dull patch near mounting point, torque drift above 3%, spindle vibration above 3.5 mm/s, or tool life above 85%. Recommended first checks: inspect Tool Station 4 tool wear, verify calibration coupon result, then review the last tool-change event.

## QI-104 burr detected
QI-104 means edge burr probability exceeded the local threshold. Common causes are worn deburr wheel, incorrect feed rate, or missing air blast. Hold affected parts and inspect adjacent bolt holes.

## Cell 2 sample telemetry
07:41 Tool Station 4 torque drift +8%; spindle vibration 4.8 mm/s; tool life 91%; coolant flow nominal. 07:44 quality camera flagged A17 upper mount bracket surface anomaly. 07:46 operator held batch 24-10-07 pending calibration.

## Release criteria
Release the held batch only when calibration drift is within +/-3%, spindle vibration is below 3.5 mm/s, a re-inspected sample passes, and the quality log includes defect photo, source station, corrective action and supervisor sign-off.

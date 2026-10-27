# Security & Automotive Safety Policy

## 1. Scope and Simulation Boundaries

**SmartPark Guardian AI** is an architectural demonstration and software engineering platform for barrier-free parking management, vehicle-session security correlation, and accident-prevention simulation.

- **No Unauthorized Vehicle Control**: This software never implements wireless relay attacks, bypasses of automotive immobilizers/security systems, or unauthenticated remote control of real vehicles.
- **No Real Braking/Steering Control**: The Accident Prevention Engine (`smartpark/routing/` and `smartpark/simulation/`) models indoor stopping distances and A* pathfinding strictly as a software simulation. It does not validate physical braking hardware and must never be wired directly to vehicle actuators.
- **Fail-Safe Access & Anti-Entrapment**:
  - **Entry**: During network or database outages, the entrance defaults to `FAILSAFE_HOLD` (never automatically opening a secure entrance solely because a server is unreachable).
  - **Exit / Egress**: During network, database, or payment-gateway outages, exit evaluation prioritizes human safety (`EMERGENCY_OVERRIDE` / audited safe egress with deferred billing reconciliation) so no person or vehicle is ever trapped. Certified physical access hardware and qualified installers remain responsible for physical safety compliance.

## 2. Cryptographic & Privacy Safeguards

- **License Plate Ownership Rule**: A detected license plate alone is never treated as proof of ownership. Barrier-free entry requires an enrolled account/vehicle relationship corroborated by an active reservation window or a verified secondary credential (simulated BLE Digital Key, RFID, or signed QR badge).
- **Walk-Up Digital Key**: BLE proximity alone never unlocks a vehicle. Unlock operations require a short-lived challenge (`WalkUpChallenge`), single-use cryptographic nonce (replay protection), rate limiting, and explicit user biometric/passcode confirmation signature.
- **Privacy-First Parking Badge**: Uses HMAC-SHA256 signed tokens with short TTLs and 30-second rolling window slots to prevent static screenshot reuse. Public badges expose only `public_badge_id` and `public_vehicle_token`—never customer PII or raw license plates.
- **Payment Security**: Raw payment card numbers (PAN) and CVVs are never collected, processed, or stored.

## 3. Reporting a Vulnerability

If you discover a security, privacy, or logic issue in this repository, please open a GitHub Security Advisory or contact the repository maintainer directly rather than posting exploitable details in a public issue.

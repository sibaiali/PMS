# Contributing to SmartPark Guardian AI

Thank you for your interest in contributing to **SmartPark Guardian AI**!

## Engineering Principles

1. **Core / GUI / Simulation Separation**: Keep domain logic (`smartpark/core`, `smartpark/access`, `smartpark/guardian`, `smartpark/billing`, `smartpark/security`, `smartpark/database`) completely independent of `tkinter` or rendering code.
2. **Honest Simulation Labeling**: Any simulated sensor, vehicle adapter, push notification, or payment gateway interaction must implement an explicit adapter interface and label its output as simulated (`is_simulated=True`).
3. **Ethical Security Design**:
   - Never classify a pedestrian as suspicious based solely on proximity or presence near a vehicle (`R_ETHICS_NO_PROXIMITY_ACCUSATION`).
   - Prefer explainable, deterministic security correlation rules over opaque models for safety-critical or security-critical decisions.
4. **Financial & Timezone Precision**: Always use `decimal.Decimal` (`ROUND_HALF_UP` to `0.01`) for monetary calculations and explicit IANA timezones (`zoneinfo.ZoneInfo`) for tariff windows.
5. **No Secrets in Git**: Never commit API keys, signing secrets, `.env` files, or `.db` SQLite files containing customer data.

## Running the Test Suite

Before submitting a Pull Request, verify that all automated tests pass:

```bash
python -m unittest discover -s tests -v
```

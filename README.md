# Smart Parking System

Advanced smart parking platform with a React frontend and a Python Flask + SQLite backend designed for real-time allocation, IoT integration, predictive availability, and user-centric booking.

## What Changed

- Weather-aware slot allocation using OpenWeather with cached lookups at allocation time
- Emergency vehicle prioritization for ambulance, fire truck, and police vehicles
- Scalable multi-zone, multi-floor slot generation with rich metadata
- Floor scoring based on occupancy, exit distance, and user preferences
- Predictive parking insights from historical occupancy logs
- Booking expiry worker that frees slots after the grace period
- QR-based gate entry validation and simulated gate opening
- Indoor navigation guidance with multiple route variations
- Live admin analytics for peak hours, floor load, and zone load
- Polling-based live updates for bookings, gate events, and slot state changes

## Current Structure

```text
smart-parking/
|-- backend/
|   |-- app.py              # Active Python Flask backend
|   |-- python_backend/     # Smart allocation, QR, prediction, IoT logic
|   |-- .env.example
|   `-- requirements.txt
|-- frontend/               # React dashboard and admin console
|-- hardware/
|   `-- arduino/
|       `-- SmartParking.ino
`-- package.json
```

## Backend Stack

- Python + Flask
- SQLite
- Polling via `/events/latest` for live slot and booking updates
- OpenWeather API for environment-aware allocation
- Built-in SVG QR rendering for booking entry passes
- Runtime target: Python 3.11.9

Optional next step:
- Redis can be added later for distributed weather and slot caching. The current implementation uses in-memory caching to keep the project runnable without extra infrastructure.

## Quick Start

### 1. Backend

```powershell
cd backend
copy .env.example .env
py -3.11 -m pip install -r requirements.txt
py -3.11 app.py
```

To regenerate demo data with `100` slots split into `4` floors and fresh sample bookings/logs:

```powershell
cd backend
py -3.11 seed_sample_data.py
```

### 2. Frontend

```powershell
cd frontend
npm install
npm start
```

Frontend runs at `http://localhost:3000` and talks to the backend on `http://localhost:5000`.

## Main API Surface

- `POST /auth/register`
- `POST /auth/login`
- `GET /auth/me`
- `GET /slots`
- `GET /parking/context`
- `POST /parking/recommendation`
- `POST /bookings`
- `GET /bookings/my`
- `POST /bookings/:bookingId/checkin`
- `POST /bookings/:bookingId/checkout`
- `POST /gate/scan-qr`
- `POST /iot/slot-state`
- `GET /admin/overview`

## Notes

- The active backend is [app.py](/d:/Downloads/smart-parking-system/smart-parking/backend/app.py).
- The `backend/src/` Node service from the earlier migration is no longer required for the app to run.
- If `OPENWEATHER_API_KEY` is not configured, weather-aware allocation falls back to a normal-weather profile.
- The booking expiry worker runs every minute and frees stale pending bookings automatically.
- Keep `SIMULATION_MODE=true` in [backend/.env](/d:/Downloads/smart-parking-system/smart-parking/backend/.env) unless your Arduino is actually connected on the configured serial port.

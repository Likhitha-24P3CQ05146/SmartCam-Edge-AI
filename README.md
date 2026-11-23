# SmartCam Edge AI — v1.4

A local-first React + FastAPI prototype for recorded-video analysis, optional AI object detection, motion anomaly screening, review workflow, SQLite history, CSV exports, and real-time notifications.

## Quick Start

### 1. Backend (Python 3.11+)

Open PowerShell in the project folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Backend runs at: `http://127.0.0.1:8000`

### 2. Frontend (Node.js LTS)

Open a second terminal in the same project folder:

```powershell
npm install
npm run dev
```

Open: `http://127.0.0.1:5173`

Upload an MP4, MOV, AVI, or WebM recording (up to 100 MB).

## Optional AI Object Detection

Basic video quality analysis and notifications work without optional AI dependencies.

To enable pretrained object detection:

1. Install AI dependencies:

   ```powershell
   pip install -r requirements-ai.txt
   ```

2. Obtain the official `yolo11n.pt` weights from Ultralytics, subject to its licensing and model usage terms.
3. Create a `models` folder in the project directory and place `yolo11n.pt` inside it.
4. Restart the backend.

Object labels and counts are aggregated over sampled frames, not unique objects. The system does not recognize identities or track people.

## Real-Time Notifications

The backend stores notification events in SQLite. The React dashboard listens to `/api/notifications/stream` using Server-Sent Events (SSE) and displays in-app alerts.

The browser must remain open for these in-app notifications.

## Potential Abnormal Activity (Motion Screening)

The system screens uploaded videos for unusual changes in motion relative to the sampled-frame baseline.

It provides approximate timestamps and anomaly scores for moments requiring human review.

**Important:** This is a motion anomaly heuristic, not a trained action-recognition model. It cannot identify falling, fighting, trespassing, or crime. False positives and missed events are possible. Do not use it for safety-critical decisions.

## Main APIs

- `GET /api/health`
- `GET /api/model-status`
- `POST /api/analyze`
- `GET /api/analyses`
- `POST /api/analyses/{id}/review`
- `GET /api/summary`
- `GET /api/notifications`
- `GET /api/notifications/stream`
- `GET /api/report.csv`

## Mobile Browser Push Notifications (Web Push)

The project includes optional browser Web Push support. It is not SMS or a native mobile application.

Push delivery depends on browser support, HTTPS, notification permissions, device subscription, and the backend remaining online.

### Setup (Windows PowerShell)

1. Install backend dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Generate VAPID keys:

   ```powershell
   python generate_push_keys.py
   ```

3. Keep the private key in a local file such as `vapid_private.pem`. Never commit it to Git.

4. Set the environment variables in the same terminal used to start the backend:

   ```powershell
   $env:SMARTCAM_VAPID_PRIVATE_KEY = (Resolve-Path .\vapid_private.pem).Path
   $env:SMARTCAM_VAPID_PUBLIC_KEY = 'PASTE_GENERATED_PUBLIC_KEY'
   $env:SMARTCAM_VAPID_SUBJECT = 'mailto:your-real-email@example.com'
   $env:SMARTCAM_PUSH_ENROLL_TOKEN = 'CHOOSE_A_LONG_RANDOM_SECRET'
   python -m uvicorn main:app --reload
   ```

   Replace the sample values with your own values. Do not commit secrets.

5. Start the frontend in a second terminal:

   ```powershell
   npm install
   npm run dev
   ```

6. For phone testing, deploy the frontend and backend behind the same HTTPS origin. Configure the `/api` path to reach FastAPI.

7. On supported Android browsers, open the HTTPS website, enter the enrollment token, enable notifications, and allow browser notification permission. On supported iOS/iPadOS versions, use Safari's Add to Home Screen option before enabling notifications.

Push notifications are best effort. Only warning events trigger mobile push.

## Limitations

- Supports authorized, prerecorded video only.
- No live CCTV feed support.
- No identity recognition or person tracking.
- No security-grade threat detection.
- Motion anomaly results can contain false positives or miss events.
- Uploaded videos are temporary; metadata and event records are stored.
- The local API does not include user authentication. Do not expose it publicly without proper authentication, access controls, rate limits, and secure deployment.

## Security Notes

- Never upload private VAPID keys, passwords, enrollment tokens, or `.env` files.
- Use consented test footage.
- Keep private credentials outside the public repository.
- Do not expose the unauthenticated prototype directly to the internet.
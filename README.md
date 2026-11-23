# SmartCam Edge AI — v1.4

A local-first React + FastAPI prototype with recorded-video quality checks, optional pretrained general-object recognition, review workflow, SQLite history, CSV exports, and real-time in-app notifications via Server-Sent Events (SSE).

## Quick start

**Backend** (Python 3.11+):

```bash
cd backend
python -m venv .venv
# Windows CMD: .venv\Scripts\activate.bat
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

**Frontend** (Node.js LTS):

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. Use an MP4, MOV, AVI, or WebM recording up to 100 MB.

## Optional AI object detection

Quality analysis and notifications work **without** installing AI dependencies. To enable pretrained detection:

1. Install `pip install -r requirements-ai.txt` in the backend virtual environment.
2. Obtain the official `yolo11n.pt` weights from Ultralytics, subject to their licensing and model usage terms. Save the file at `backend/models/yolo11n.pt` (create the `models` directory). The project **does not automatically download weights**.
3. Restart the backend. The dashboard will show **AI model ready**.

Object labels/counts are aggregated over up to 24 sampled frames, **not unique objects**. The system does not recognize identities or track people. Processing speed depends on CPU performance.

## Real-time notifications

When analysis completes or review status changes, the backend stores an event in SQLite. The React dashboard listens to `/api/notifications/stream` using SSE and shows in-app alerts. The browser must remain open; there are no push notifications or emails.

## New in v1.4: potential abnormal activity (motion screening)

Every uploaded video is screened for **unusual changes in motion** relative to its own sampled-frame baseline. The result contains `anomaly_events` with approximate timestamps and `anomaly_score`, and the dashboard displays moments requiring human review. An SSE notification is generated when such a moment is detected. The feature runs without the optional YOLO model.

**Important:** This is an explainable, baseline-based **motion anomaly heuristic**, not a trained action-recognition model. It cannot tell whether someone is falling, fighting, trespassing, or committing a crime. Scene changes, camera movement, compression artifacts, or ordinary busy activity may cause false positives; subtle events may be missed. It only analyzes sampled frames (up to 120), not every frame. Use consented test footage, and do not rely on it for safety-critical decisions.

## Main APIs

- `GET /api/health`
- `GET /api/model-status`
- `POST /api/analyze` (multipart `video`)
- `GET /api/analyses`
- `POST /api/analyses/{id}/review`
- `GET /api/summary`
- `GET /api/notifications`
- `GET /api/notifications/stream`
- `GET /api/report.csv`

## Limitations

This is a prototype for authorized, prerecorded video. No live camera feeds, identity recognition, person tracking, security-grade threat detection, login, or browser push notifications. Uploaded video files are temporary and deleted after analysis; only metadata and event records remain. Quality checks and detections can be inaccurate. The local API is unauthenticated and must not be exposed to the internet.

## New in v1.4 — mobile browser push notifications (Web Push)

**This is real browser Web Push, not SMS or a native Android/iOS app.** The backend uses VAPID and pywebpush. Push notifications can arrive after the dashboard tab closes **if** the phone browser supports push, the site is installed as required, notifications are allowed, and the backend remains online. Push delivery is best effort.

### Setup (Windows PowerShell)

1. Install backend dependencies: `cd backend` then `python -m pip install -r requirements.txt`.
2. Generate VAPID keys: `python generate_push_keys.py`. Save the printed PEM private key in `backend/vapid_private.pem` **without committing it to Git**. Copy the printed public key.
3. Set environment variables in the **same terminal** where you run uvicorn:

   ```powershell
   $env:SMARTCAM_VAPID_PRIVATE_KEY = (Resolve-Path .\vapid_private.pem).Path
   $env:SMARTCAM_VAPID_PUBLIC_KEY = 'PASTE_GENERATED_PUBLIC_KEY'
   $env:SMARTCAM_VAPID_SUBJECT = 'mailto:your-real-email@example.com'
   $env:SMARTCAM_PUSH_ENROLL_TOKEN = 'CHOOSE_A_LONG_RANDOM_SECRET'
   python -m uvicorn main:app --reload
   ```

   Replace the sample email with a real contact address, and generate a strong random enrollment token. Do not commit these values. The backend reads these settings at startup.
4. Run the frontend with `cd frontend`, `npm install`, `npm run dev`. Visit on your computer at `http://localhost:5173` to test registration. localhost is a secure-context exception; phones are not.
5. **For a real phone**, deploy frontend and backend behind the **same HTTPS origin** (reverse proxy `/api` to FastAPI). The backend must remain running and reachable; localhost on your laptop is not reachable from a phone outside that laptop. The current app has no user authentication, so **do not expose this prototype publicly** without adding authentication, access control, rate limits and secure deployment first. A private HTTPS deployment with appropriate access restrictions is recommended for testing.
6. On Android Chrome, open the HTTPS site, enter enrollment token, tap **Enable on this device**, allow notifications. On iPhone/iPad, use Safari → Share → **Add to Home Screen**, open the installed app, then enable notifications there (supported iOS/iPadOS versions only).
7. Analyze a video that produces a warning. The backend attempts a push to subscribed devices. It sends a short text alert only, not footage. The existing in-app SSE notifications still work.

### Notes and limitations

- **No scheduled processing:** an uploaded video must be analyzed before an alert can be generated. No live CCTV support yet.
- **Not a semantic activity detector:** motion anomalies can be false positives; this does not recognize crime, intent, or a particular human action.
- **Only warnings trigger mobile push**; ordinary successful analyses and review updates remain in-app.
- Push subscriptions are stored in SQLite. Invalid (404/410) subscriptions are cleaned up.
- The enrollment token limits who can subscribe but **is not a replacement for full user authentication**. Do not reuse it as a public password.
- The VAPID private key must remain secret. Changing keys invalidates existing subscriptions.
- To disable on a device, enter the token and click **Disable**. Revoke an exposed token by rotating it and restarting the backend.

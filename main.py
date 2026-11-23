from pathlib import Path
from datetime import datetime, timezone
import csv
import io
import json
import sqlite3
import tempfile
import uuid
import asyncio
import os
import logging
from urllib.parse import urlparse
from pydantic import BaseModel
from fastapi import Header
from collections import Counter

import cv2
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

BASE = Path(__file__).resolve().parent
DB = BASE / 'smartcam.db'
MAX_SIZE = 100 * 1024 * 1024
ALLOWED = {'.mp4', '.mov', '.avi', '.webm'}
app = FastAPI(title='SmartCam Edge AI API', version='1.4')
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173','http://127.0.0.1:5173'], allow_methods=['GET','POST'], allow_headers=['*'])

def connect():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def initialize():
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, created_at TEXT NOT NULL,
            duration REAL, fps REAL, width INTEGER, height INTEGER, frames_sampled INTEGER,
            avg_brightness REAL, avg_sharpness REAL, dark_ratio REAL, blurry_ratio REAL,
            status TEXT, notes TEXT)''')
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS notifications (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            kind TEXT NOT NULL, message TEXT NOT NULL, analysis_id TEXT)''')
    with connect() as conn:
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(analyses)')}
        for col, ddl in {
            'frozen_ratio': 'REAL DEFAULT 0',
            'review_status': "TEXT DEFAULT 'Unreviewed'",
            'review_note': "TEXT DEFAULT ''",
            'reviewed_at': 'TEXT',
            'detected_objects': "TEXT DEFAULT '{}'",
            'detection_frames': 'INTEGER DEFAULT 0',
            'anomaly_events': "TEXT DEFAULT '[]'",
            'anomaly_score': 'REAL DEFAULT 0',
        }.items():
            if col not in cols:
                conn.execute(f'ALTER TABLE analyses ADD COLUMN {col} {ddl}')
initialize()

# Browser Web Push (VAPID). Push delivery requires a reachable HTTPS service,
# a running backend, and a browser/OS that supports background push.
PUSH_PRIVATE_KEY = os.environ.get('SMARTCAM_VAPID_PRIVATE_KEY', '')
PUSH_PUBLIC_KEY = os.environ.get('SMARTCAM_VAPID_PUBLIC_KEY', '')
PUSH_SUBJECT = os.environ.get('SMARTCAM_VAPID_SUBJECT', '')
PUSH_ENROLL_TOKEN = os.environ.get('SMARTCAM_PUSH_ENROLL_TOKEN', '')
PUSH_ENABLED = all([PUSH_PRIVATE_KEY, PUSH_PUBLIC_KEY, PUSH_SUBJECT, PUSH_ENROLL_TOKEN])
log = logging.getLogger('smartcam.push')

with connect() as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS push_subscriptions (
        endpoint TEXT PRIMARY KEY, subscription_json TEXT NOT NULL,
        created_at TEXT NOT NULL)''')

class SubscriptionPayload(BaseModel):
    subscription: dict

@app.get('/api/push/config')
def push_config():
    return {'enabled': PUSH_ENABLED, 'public_key': PUSH_PUBLIC_KEY if PUSH_ENABLED else '',
            'message': 'Push configured' if PUSH_ENABLED else 'Set VAPID keys and enrollment token on backend'}

def check_enroll(token):
    import secrets
    if not PUSH_ENABLED:
        raise HTTPException(503, 'Mobile push is not configured on the backend.')
    if not token or not secrets.compare_digest(token, PUSH_ENROLL_TOKEN):
        raise HTTPException(403, 'Invalid enrollment token.')

@app.post('/api/push/subscribe')
def push_subscribe(payload: SubscriptionPayload, x_push_enroll_token: str | None = Header(default=None)):
    check_enroll(x_push_enroll_token)
    sub = payload.subscription
    endpoint = sub.get('endpoint', '')
    keys = sub.get('keys', {})
    parsed = urlparse(endpoint)
    if (parsed.scheme != 'https' or not parsed.netloc or len(endpoint) > 2048
            or not isinstance(keys, dict) or not keys.get('p256dh') or not keys.get('auth')):
        raise HTTPException(400, 'Invalid push subscription.')
    with connect() as conn:
        conn.execute('INSERT OR REPLACE INTO push_subscriptions VALUES (?,?,?)',
                     (endpoint, json.dumps(sub), datetime.now(timezone.utc).isoformat()))
    return {'subscribed': True}

@app.post('/api/push/unsubscribe')
def push_unsubscribe(payload: SubscriptionPayload, x_push_enroll_token: str | None = Header(default=None)):
    check_enroll(x_push_enroll_token)
    endpoint = payload.subscription.get('endpoint', '')
    with connect() as conn:
        conn.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (endpoint,))
    return {'unsubscribed': True}

def send_mobile_push(kind, message, analysis_id):
    if not PUSH_ENABLED or kind != 'warning':
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning('pywebpush not installed; mobile push skipped')
        return
    with connect() as conn:
        subs = [dict(r) for r in conn.execute('SELECT * FROM push_subscriptions')]
    for sub in subs:
        try:
            webpush(subscription_info=json.loads(sub['subscription_json']),
                    data=json.dumps({'title':'SmartCam Edge AI – Review needed',
                                     'body':message[:180], 'analysis_id':analysis_id}),
                    vapid_private_key=PUSH_PRIVATE_KEY,
                    vapid_claims={'sub': PUSH_SUBJECT}, ttl=3600, timeout=8)
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                with connect() as conn:
                    conn.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (sub['endpoint'],))
            else:
                log.warning('Push delivery failed: %s', type(exc).__name__)
        except Exception as exc:
            log.warning('Push delivery error: %s', type(exc).__name__)

# Optional, general-purpose, general-purpose object recognition on user-supplied recordings.
# No face recognition, identification, or person tracking.
MODEL_PATH = BASE / 'models' / 'yolo11n.pt'
_detector = None

def load_detector():
    global _detector
    if _detector is not None:
        return _detector
    if not MODEL_PATH.is_file():
        return None
    try:
        from ultralytics import YOLO
        _detector = YOLO(str(MODEL_PATH))
        return _detector
    except (ImportError, RuntimeError, ValueError, OSError):
        return None

def detect_objects(frame):
    model = load_detector()
    if model is None:
        return []
    predictions = model.predict(source=frame, imgsz=416, conf=0.4, verbose=False, device='cpu')
    if not predictions:
        return []
    result = predictions[0]
    counts = Counter()
    for box in result.boxes:
        class_id = int(box.cls[0])
        name = str(result.names[class_id])
        # Only aggregate counts; no boxes, identities, or tracking saved.
        counts[name] += 1
    return list(counts.elements())

def notify(kind, message, analysis_id=None):
    with connect() as conn:
        conn.execute('INSERT INTO notifications (created_at,kind,message,analysis_id) VALUES (?,?,?,?)',
                     (datetime.now(timezone.utc).isoformat(),kind,message,analysis_id))
    send_mobile_push(kind, message, analysis_id)

@app.get('/api/model-status')
def model_status():
    return {'available':load_detector() is not None,
            'message':'Object detection enabled' if load_detector() is not None else
            'Optional model not installed. Quality checks still work.'}

@app.get('/api/notifications')
def notifications(after: int = 0):
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM notifications WHERE seq>? ORDER BY seq ASC LIMIT 100',(max(0,after),))]

@app.get('/api/notifications/stream')
async def notification_stream(after: int = 0):
    async def events():
        cursor = max(0,after)
        while True:
            with connect() as conn:
                rows = [dict(r) for r in conn.execute(
                    'SELECT * FROM notifications WHERE seq>? ORDER BY seq ASC LIMIT 100',(cursor,))]
            for row in rows:
                cursor = row['seq']
                yield 'id: ' + str(cursor) + '\ndata: ' + json.dumps(row) + '\n\n'
            yield ': heartbeat\n\n'
            await asyncio.sleep(1.5)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.get('/api/health')
def health():
    return {'status':'healthy','service':'SmartCam Edge AI'}

@app.get('/api/analyses')
def list_analyses():
    with connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM analyses ORDER BY created_at DESC LIMIT 100')]

@app.get('/api/summary')
def summary():
    with connect() as conn:
        r = conn.execute('SELECT COUNT(*) AS total, SUM(CASE WHEN status = "Needs review" THEN 1 ELSE 0 END) AS flagged, SUM(frames_sampled) AS frames FROM analyses').fetchone()
    return {'total_analyses':r['total'] or 0,'flagged_analyses':r['flagged'] or 0,'sampled_frames':r['frames'] or 0}

@app.post('/api/analyze')
async def analyze(video: UploadFile = File(...)):
    suffix = Path(video.filename or '').suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, 'Use MP4, MOV, AVI, or WebM video.')
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            path = Path(tmp.name)
            total = 0
            while chunk := await video.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_SIZE:
                    raise HTTPException(413, 'Video must be 100 MB or smaller.')
                tmp.write(chunk)
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise HTTPException(400, 'Unable to decode this video.')
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            if fps <= 0 or fps > 240:
                fps = 0
            duration = count / fps if fps else 0
            # Sample up to 120 evenly spaced frames, avoiding heavy processing.
            step = max(1, count // 120) if count else 15
            index = 0
            brightness = []
            sharpness = []
            previous_small = None
            object_counts = Counter()
            detection_frames = 0
            frozen_pairs = 0
            compared_pairs = 0
            motion_samples = []
            previous_motion = None
            while len(brightness) < 120:
                ok, frame = cap.read()
                if not ok:
                    break
                if index % step == 0:
                    h, w = frame.shape[:2]
                    scale = min(1, 640 / max(h, w))
                    if scale < 1:
                        frame = cv2.resize(frame, None, fx=scale, fy=scale)
                    # Run optional detection on at most 24 sampled frames.
                    if detection_frames < 24 and load_detector() is not None:
                        object_counts.update(detect_objects(frame))
                        detection_frames += 1
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    brightness.append(float(gray.mean()))
                    sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
                    small = cv2.resize(gray, (64, 36))
                    if previous_small is not None:
                        compared_pairs += 1
                        # Near-identical samples can indicate a frozen feed,
                        # but a stationary scene can also produce this result.
                        if float(cv2.absdiff(small, previous_small).mean()) < 0.5:
                            frozen_pairs += 1
                    previous_small = small
                    # Motion-change heuristic: compare successive sampled frames.
                    # Ignore large whole-scene changes and lighting jumps, which
                    # often indicate cuts or camera changes rather than activity.
                    motion_gray = cv2.resize(gray, (160, 90))
                    if previous_motion is not None:
                        diff = cv2.absdiff(motion_gray, previous_motion)
                        changed = float((diff > 25).mean())
                        brightness_jump = abs(float(motion_gray.mean()) - float(previous_motion.mean()))
                        sample_time = index / fps if fps else float(len(brightness) - 1)
                        if changed < 0.75 and brightness_jump < 30:
                            motion_samples.append((round(sample_time, 2), changed))
                    previous_motion = motion_gray
                index += 1
            if not brightness:
                raise HTTPException(400, 'No readable frames found in the video.')
        finally:
            cap.release()
        dark_ratio = sum(v < 45 for v in brightness) / len(brightness)
        blurry_ratio = sum(v < 60 for v in sharpness) / len(sharpness)
        frozen_ratio = frozen_pairs / compared_pairs if compared_pairs else 0.0
        # Flag short periods of unusually large motion relative to this
        # recording's own baseline. This is NOT semantic action recognition.
        import statistics
        anomaly_events = []
        anomaly_score = 0.0
        if len(motion_samples) >= 8:
            values = [v for _, v in motion_samples]
            baseline = statistics.median(values)
            mad = statistics.median(abs(v - baseline) for v in values)
            threshold = max(0.12, baseline + max(0.08, 4 * mad))
            last_event = -100000.0
            for at, value in motion_samples:
                if value >= threshold and at - last_event >= 2.0:
                    anomaly_events.append({
                        'timestamp_seconds': at,
                        'type': 'unusual_motion',
                        'description': 'Motion change above this video baseline; review the footage.',
                        'motion_ratio': round(value, 3),
                    })
                    last_event = at
            anomaly_events = anomaly_events[:20]
            anomaly_score = round(max(values) * 100, 1)
        notes = []
        if anomaly_events:
            notes.append(f'{len(anomaly_events)} unusual-motion moment(s) detected; manual review required.')
        if dark_ratio >= .3:
            notes.append('Low-light frames detected; review lighting conditions.')
        if blurry_ratio >= .3:
            notes.append('Low-sharpness frames detected; review focus or motion blur.')
        if compared_pairs >= 5 and frozen_ratio >= .8:
            notes.append('Mostly unchanged sampled frames; check for a frozen feed or stationary scene.')
        if not notes:
            notes.append('No major quality issues detected by the basic checks.')
        record = {
            'id': str(uuid.uuid4()), 'filename': Path(video.filename or 'video').name[:200],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'duration': round(duration,2), 'fps': round(fps,2), 'width':width,'height':height,
            'frames_sampled':len(brightness),'avg_brightness':round(sum(brightness)/len(brightness),1),
            'avg_sharpness':round(sum(sharpness)/len(sharpness),1),
            'dark_ratio':round(dark_ratio,3),'blurry_ratio':round(blurry_ratio,3),
            'status':'Needs review' if anomaly_events or dark_ratio >= .3 or blurry_ratio >= .3 or (compared_pairs >= 5 and frozen_ratio >= .8) else 'Good',
            'notes':' '.join(notes),
            'frozen_ratio': round(frozen_ratio, 3),
            'review_status': 'Unreviewed', 'review_note': '', 'reviewed_at': None,
            'detected_objects': json.dumps(dict(object_counts)), 'detection_frames': detection_frames,
            'anomaly_events': json.dumps(anomaly_events), 'anomaly_score': anomaly_score
        }
        with connect() as conn:
            columns = ','.join(record)
            conn.execute(f'INSERT INTO analyses ({columns}) VALUES (' + ','.join('?' for _ in record) + ')', tuple(record.values()))
        if anomaly_events:
            notify('warning', f"Unusual motion detected in {record['filename']} at " +
                   ', '.join(f"{e['timestamp_seconds']:.1f}s" for e in anomaly_events[:3]) +
                   (' (and more)' if len(anomaly_events) > 3 else '') + '. Review required.', record['id'])
        notify('warning' if record['status']=='Needs review' else 'success',
               f"Analysis complete: {record['filename']} — {record['status']}",record['id'])
        return record
    finally:
        await video.close()
        if path:
            path.unlink(missing_ok=True)

@app.post('/api/analyses/{analysis_id}/review')
async def review_analysis(analysis_id: str, payload: dict):
    status = payload.get('review_status')
    note = payload.get('review_note', '')
    if status not in ('Unreviewed', 'Acknowledged', 'Resolved'):
        raise HTTPException(400, 'Invalid review status.')
    if not isinstance(note, str) or len(note) > 500:
        raise HTTPException(400, 'Review note must be 500 characters or fewer.')
    timestamp = datetime.now(timezone.utc).isoformat() if status != 'Unreviewed' else None
    with connect() as conn:
        result = conn.execute('UPDATE analyses SET review_status=?, review_note=?, reviewed_at=? WHERE id=?',
                              (status, note, timestamp, analysis_id))
        if result.rowcount == 0:
            raise HTTPException(404, 'Analysis not found.')
        updated = dict(conn.execute('SELECT * FROM analyses WHERE id=?', (analysis_id,)).fetchone())
    notify('review', f"Review updated: {updated['filename']} — {status}", analysis_id)
    return updated

@app.get('/api/report.csv')
def report():
    with connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM analyses ORDER BY created_at DESC')]
    output = io.StringIO()
    fields = ['id','filename','created_at','duration','fps','width','height','frames_sampled','avg_brightness','avg_sharpness','dark_ratio','blurry_ratio','frozen_ratio','status','notes','review_status','review_note','reviewed_at','detected_objects','detection_frames','anomaly_events','anomaly_score']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="smartcam-report.csv"'})

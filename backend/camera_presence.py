from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    import numpy as np
except ImportError:  # pragma: no cover - runtime dependency on the Pi
    np = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - runtime dependency on the Pi
    Image = None

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - optional backend credential support
    GoogleAuthRequest = None
    service_account = None


YOLOV8N_ONNX_URL = "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx"
PERSON_CLASS_ID = 0
_google_credentials: Any = None
_google_credentials_path = ""


def _service_account_token() -> str:
    global _google_credentials, _google_credentials_path
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path or service_account is None or GoogleAuthRequest is None:
        return ""
    if _google_credentials is None or _google_credentials_path != credentials_path:
        _google_credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        _google_credentials_path = credentials_path
    if not _google_credentials.valid:
        _google_credentials.refresh(GoogleAuthRequest())
    return str(_google_credentials.token or "")


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _label_for_state(
    state: str,
    alert: bool = False,
    *,
    stale: bool = False,
    low_light: bool = False,
) -> str:
    if alert:
        return "ALARM"
    # Distinguish recoverable uplink loss from genuine darkness so the kiosk
    # does not keep shouting "blind" when Chrome dropped the self-signed cert.
    if state == "camera_blind":
        if stale:
            return "Ingen snapshots"
        if low_light:
            return "For mørkt"
        return "Ingen snapshots"
    return {
        "empty": "Ingen hjemme",
        "checking": "Tjekker...",
        "home": "Nogen hjemme",
        "unknown": "Ukendt",
        "camera_blind": "Ingen snapshots",
    }.get(state, "Ukendt")


def _event_id(prefix: str = "person") -> str:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


def _firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_firestore_value(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(k): _firestore_value(v) for k, v in value.items()}}}
    return {"stringValue": str(value)}


@dataclass
class MotionResult:
    score: float
    candidate: bool
    low_light: bool
    mean_luminance: float
    baseline_reset: bool = False


@dataclass
class PersonResult:
    ok: bool
    confidence: float = 0.0
    bbox: list[float] | None = None
    elapsed_ms: float = 0.0
    status: str = "disabled"
    error: str = ""


class MotionGate:
    def __init__(
        self,
        baseline_file: Path,
        width: int = 160,
        height: int = 90,
        candidate_threshold: float = 0.035,
        pixel_threshold: int = 35,
        low_light_threshold: float = 18.0,
        reset_threshold: float = 0.65,
        reset_frames: int = 6,
    ) -> None:
        self.baseline_file = baseline_file
        self.width = width
        self.height = height
        self.candidate_threshold = candidate_threshold
        self.pixel_threshold = pixel_threshold
        self.low_light_threshold = low_light_threshold
        self.reset_threshold = reset_threshold
        self.reset_frames = reset_frames

    def relearn(self, body: bytes) -> bool:
        """Adopt a stable frame after ML rejected sustained motion."""
        if Image is None or np is None:
            return False
        try:
            import io

            with Image.open(io.BytesIO(body)) as img:
                gray = img.convert("L").resize((self.width, self.height))
                arr = np.asarray(gray, dtype=np.float32)
            mean = float(arr.mean()) if arr.size else 0.0
            normalized = np.clip(arr + (128.0 - mean), 0, 255).astype(np.uint8)
            self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
            self.baseline_file.write_bytes(normalized.tobytes())
            return True
        except Exception:
            return False

    def analyze(self, body: bytes, *, allow_baseline_update: bool, state: dict[str, Any]) -> MotionResult:
        if Image is None or np is None:
            return MotionResult(0.0, False, False, 0.0)
        try:
            import io

            with Image.open(io.BytesIO(body)) as img:
                gray = img.convert("L").resize((self.width, self.height))
                arr = np.asarray(gray, dtype=np.float32)
        except Exception:
            return MotionResult(0.0, False, False, 0.0)

        mean = float(arr.mean()) if arr.size else 0.0
        low_light = mean < self.low_light_threshold
        normalized = np.clip(arr + (128.0 - mean), 0, 255).astype(np.uint8)
        current = normalized.tobytes()
        expected = self.width * self.height
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.baseline_file.is_file():
            self.baseline_file.write_bytes(current)
            return MotionResult(0.0, False, low_light, mean)
        try:
            baseline = np.frombuffer(self.baseline_file.read_bytes(), dtype=np.uint8).copy()
        except OSError:
            baseline = np.array([], dtype=np.uint8)
        if baseline.size != expected:
            self.baseline_file.write_bytes(current)
            return MotionResult(0.0, False, low_light, mean, baseline_reset=True)

        cur = np.frombuffer(current, dtype=np.uint8)
        changed = np.abs(cur.astype(np.int16) - baseline.astype(np.int16)) > self.pixel_threshold
        score = float(changed.mean())
        candidate = score >= self.candidate_threshold

        baseline_shift_frames = int(state.get("baselineShiftFrames") or 0)
        baseline_reset = False
        if score >= self.reset_threshold:
            baseline_shift_frames += 1
        else:
            baseline_shift_frames = max(0, baseline_shift_frames - 1)
        state["baselineShiftFrames"] = baseline_shift_frames
        if baseline_shift_frames >= self.reset_frames:
            self.baseline_file.write_bytes(current)
            state["baselineShiftFrames"] = 0
            baseline_reset = True
            candidate = False
            score = 0.0
        elif allow_baseline_update and not candidate:
            updated = (baseline.astype(np.float32) * 0.985 + cur.astype(np.float32) * 0.015).astype(np.uint8)
            with contextlib_suppress_oserror():
                self.baseline_file.write_bytes(updated.tobytes())

        return MotionResult(score, candidate, low_light, mean, baseline_reset)


class contextlib_suppress_oserror:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


class PersonDetector:
    def __init__(
        self,
        model_path: Path,
        model_url: str = YOLOV8N_ONNX_URL,
        confidence_threshold: float = 0.38,
        input_size: int = 640,
    ) -> None:
        self.model_path = model_path
        self.model_url = model_url
        self.confidence_threshold = confidence_threshold
        self.input_size = input_size
        self._session: Any = None
        self._input_name = ""
        self._status = "not_loaded"
        self._error = ""

    @property
    def status(self) -> str:
        return self._status

    def _ensure_model(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        if self.model_path.is_file() and self.model_path.stat().st_size > 1_000_000:
            return
        with urllib.request.urlopen(self.model_url, timeout=90) as response:
            self.model_path.write_bytes(response.read())

    def _ensure_session(self) -> bool:
        if self._session is not None:
            return True
        if Image is None or np is None:
            self._status = "missing_pillow_or_numpy"
            return False
        try:
            import onnxruntime as ort

            self._ensure_model()
            self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
            self._input_name = self._session.get_inputs()[0].name
            self._status = "ready"
            self._error = ""
            return True
        except Exception as exc:
            self._status = "error"
            self._error = str(exc)
            return False

    def detect(self, body: bytes) -> PersonResult:
        if not self._ensure_session():
            return PersonResult(ok=False, status=self._status, error=self._error)
        try:
            import io

            start = _now()
            with Image.open(io.BytesIO(body)) as img:
                img = img.convert("RGB")
                orig_w, orig_h = img.size
                scale = min(self.input_size / orig_w, self.input_size / orig_h)
                resized_w = int(round(orig_w * scale))
                resized_h = int(round(orig_h * scale))
                resized = img.resize((resized_w, resized_h))
                canvas = Image.new("RGB", (self.input_size, self.input_size), (114, 114, 114))
                dx = (self.input_size - resized_w) // 2
                dy = (self.input_size - resized_h) // 2
                canvas.paste(resized, (dx, dy))
                arr = np.asarray(canvas).astype(np.float32) / 255.0
            inp = np.transpose(arr, (2, 0, 1))[None]
            output = self._session.run(None, {self._input_name: inp})[0]
            pred = output[0] if output.ndim == 3 else output
            if pred.shape[0] < pred.shape[1]:
                pred = pred.T

            best_conf = 0.0
            best_box: list[float] | None = None
            for row in pred:
                values = row.astype(float)
                if values.shape[0] >= 85:
                    conf = float(values[4] * values[5 + PERSON_CLASS_ID])
                else:
                    conf = float(values[4 + PERSON_CLASS_ID])
                if conf <= best_conf:
                    continue
                x, y, w, h = values[:4]
                x1 = max(0.0, (x - w / 2 - dx) / scale)
                y1 = max(0.0, (y - h / 2 - dy) / scale)
                x2 = min(float(orig_w), (x + w / 2 - dx) / scale)
                y2 = min(float(orig_h), (y + h / 2 - dy) / scale)
                best_conf = conf
                best_box = [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]

            elapsed_ms = (_now() - start) * 1000
            self._status = "ready"
            self._error = ""
            return PersonResult(
                ok=best_conf >= self.confidence_threshold,
                confidence=max(0.0, min(1.0, best_conf)),
                bbox=best_box,
                elapsed_ms=elapsed_ms,
                status="ready",
            )
        except Exception as exc:
            self._status = "error"
            self._error = str(exc)
            return PersonResult(ok=False, status="error", error=str(exc))


class CameraPresenceService:
    def __init__(
        self,
        camera_dir: Path,
        snapshot_file: Path,
        state_file: Path,
        baseline_file: Path,
        model_path: Path,
        max_stale_seconds: int = 20,
        home_timeout_seconds: int = 10 * 60,
        confirmation_window_seconds: int = 20,
        checking_timeout_seconds: int = 20,
        required_confirmations: int = 2,
        detector_health_interval_seconds: int = 60,
        evidence_cooldown_seconds: int = 10 * 60,
    ) -> None:
        self.camera_dir = camera_dir
        self.snapshot_file = snapshot_file
        self.state_file = state_file
        self.evidence_dir = camera_dir / "events"
        self.max_stale_seconds = max_stale_seconds
        self.home_timeout_seconds = home_timeout_seconds
        self.confirmation_window_seconds = confirmation_window_seconds
        self.checking_timeout_seconds = checking_timeout_seconds
        self.required_confirmations = required_confirmations
        self.detector_health_interval_seconds = detector_health_interval_seconds
        self.evidence_cooldown_seconds = evidence_cooldown_seconds
        self.motion = MotionGate(
            baseline_file=baseline_file,
            candidate_threshold=_as_float(os.getenv("HUB_CAMERA_MOTION_THRESHOLD"), 0.035),
        )
        self.detector = PersonDetector(
            model_path=model_path,
            confidence_threshold=_as_float(os.getenv("HUB_CAMERA_PERSON_THRESHOLD"), 0.38),
        )

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.is_file():
            return {"presence": "unknown", "armed": False, "alert": False}
        try:
            loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {"presence": "unknown", "armed": False, "alert": False}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def public_state(self) -> dict[str, Any]:
        state = self._read_state()
        now = _now()
        last_snapshot = _as_float(state.get("lastSnapshotAt"))
        last_person = _as_float(state.get("lastPersonAt"))
        stale = bool(last_snapshot and now - last_snapshot > self.max_stale_seconds) or not last_snapshot
        low_light = bool(state.get("lowLight"))
        detector_status = str(state.get("modelStatus") or self.detector.status)
        presence = str(state.get("presence") or "unknown")
        alert = bool(state.get("alert"))

        if stale or low_light:
            presence = "camera_blind"
        elif detector_status not in {"ready", "not_loaded"}:
            presence = "unknown"
        elif presence == "home" and last_person and now - last_person > self.home_timeout_seconds:
            presence = "empty"
            state["presence"] = presence
            state["alert"] = False
            state["confirmations"] = []
            self._write_state(state)

        return {
            **state,
            "presence": presence,
            "state": presence,
            "label": _label_for_state(presence, alert=alert, stale=stale, low_light=low_light),
            "home": presence == "home",
            "armed": bool(state.get("armed")),
            "alert": alert,
            "cameraStale": stale,
            "lowLight": low_light,
            "modelStatus": detector_status,
            "lastPersonAtIso": _iso(last_person),
            "lastMotionAtIso": _iso(_as_float(state.get("lastMotionAt"))),
            "lastSnapshotAtIso": _iso(last_snapshot),
            "lastPersonAge": max(0, now - last_person) if last_person else None,
            "lastSnapshotAge": max(0, now - last_snapshot) if last_snapshot else None,
        }

    async def set_armed(self, armed: bool, *, firebase_config: dict[str, Any] | None = None, http_client: Any = None) -> dict[str, Any]:
        state = self._read_state()
        state["armed"] = bool(armed)
        if not armed:
            state["alert"] = False
        self._write_state(state)
        public = self.public_state()
        await self.sync_firestore(public, firebase_config=firebase_config, http_client=http_client)
        return public

    async def process_snapshot(
        self,
        headers: dict[str, str],
        body: bytes,
        *,
        firebase_config: dict[str, Any] | None = None,
        http_client: Any = None,
    ) -> dict[str, Any]:
        now = _now()
        state = self._read_state()
        old_presence = str(self.public_state().get("presence") or "unknown")
        browser_score = _as_float(headers.get("x-camera-motion-score"), 0.0)
        browser_motion = str(headers.get("x-camera-motion") or "").strip().lower() in {"1", "true", "yes"}

        motion = self.motion.analyze(
            body,
            allow_baseline_update=str(state.get("presence")) != "home",
            state=state,
        )
        motion_score = max(browser_score, motion.score)
        candidate = browser_motion or motion.candidate

        last_detector_at = _as_float(state.get("lastDetectorAt"))
        health_check_due = now - last_detector_at >= self.detector_health_interval_seconds
        should_detect = bool(candidate or health_check_due)
        person = PersonResult(ok=False, status=self.detector.status)
        if should_detect and not motion.low_light:
            person = await asyncio.to_thread(self.detector.detect, body)

        confirmations = [
            float(ts)
            for ts in state.get("confirmations", [])
            if now - _as_float(ts) <= self.confirmation_window_seconds
        ]
        if person.status == "ready":
            state["personConfidence"] = person.confidence
            state["personBbox"] = person.bbox if person.ok else None
            if person.ok:
                confirmations.append(now)
        if len(confirmations) >= self.required_confirmations:
            state["lastPersonAt"] = now

        if candidate:
            state["lastMotionAt"] = now
        state["lastSnapshotAt"] = now
        state["score"] = motion_score
        state["motionScore"] = motion_score
        state["backendScore"] = motion.score
        state["browserScore"] = browser_score
        state["lowLight"] = motion.low_light
        state["meanLuminance"] = motion.mean_luminance
        state["modelStatus"] = person.status
        state["modelError"] = person.error
        state["lastDetectorAt"] = now if should_detect else last_detector_at or None
        state["detectorElapsedMs"] = person.elapsed_ms
        state["confirmations"] = confirmations
        state["cameraStale"] = False
        state["motionCandidate"] = candidate
        state["baselineReset"] = motion.baseline_reset

        detector_failed = person.status not in {"ready", "not_loaded"} and should_detect
        if motion.low_light:
            state["presence"] = "camera_blind"
            state["checkingSince"] = None
        elif detector_failed:
            state["presence"] = "unknown"
            state["checkingSince"] = None
        elif len(confirmations) >= self.required_confirmations:
            state["presence"] = "home"
            state["checkingSince"] = None
        elif candidate or confirmations:
            checking_since = _as_float(state.get("checkingSince")) or now
            state["checkingSince"] = checking_since
            if now - checking_since < self.checking_timeout_seconds:
                state["presence"] = "checking"
            else:
                baseline_relearned = self.motion.relearn(body)
                state["presence"] = "empty"
                state["alert"] = False
                state["confirmations"] = []
                state["checkingSince"] = None
                state["baselineReset"] = baseline_relearned
                state["motionCandidate"] = False
                state["score"] = 0.0
                state["motionScore"] = 0.0
                state["backendScore"] = 0.0
        elif _as_float(state.get("lastPersonAt")) and now - _as_float(state.get("lastPersonAt")) < self.home_timeout_seconds:
            state["presence"] = "home"
            state["checkingSince"] = None
        else:
            state["presence"] = "empty"
            state["alert"] = False
            state["checkingSince"] = None

        transitioned_home = old_presence != "home" and state["presence"] == "home"
        if transitioned_home:
            await self._capture_evidence(
                state,
                body,
                prefix="person",
                firebase_config=firebase_config,
                http_client=http_client,
            )

        if state.get("armed") and state["presence"] == "home":
            state["alert"] = True
            if not state.get("alertEventId"):
                await self._capture_evidence(
                    state,
                    body,
                    prefix="alert",
                    firebase_config=firebase_config,
                    http_client=http_client,
                    alert=True,
                )

        self._write_state(state)
        public = self.public_state()
        await self.sync_firestore(public, firebase_config=firebase_config, http_client=http_client)
        return public

    async def _capture_evidence(
        self,
        state: dict[str, Any],
        body: bytes,
        *,
        prefix: str,
        firebase_config: dict[str, Any] | None,
        http_client: Any,
        alert: bool = False,
    ) -> None:
        last_evidence_at = _as_float(state.get("lastEvidenceAt"))
        if not alert and last_evidence_at and _now() - last_evidence_at < self.evidence_cooldown_seconds:
            return
        event_id = _event_id(prefix)
        event_dir = self.evidence_dir / event_id
        event_dir.mkdir(parents=True, exist_ok=True)
        local_snapshot = event_dir / "snapshot.jpg"
        local_snapshot.write_bytes(body)

        metadata = {
            "eventId": event_id,
            "createdAt": _now(),
            "presence": state.get("presence"),
            "confidence": state.get("personConfidence"),
            "bbox": state.get("personBbox"),
            "motionScore": state.get("motionScore"),
            "alert": alert,
        }
        (event_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        storage_path = f"ejdersted/garden/events/{event_id}/snapshot.jpg"
        url = await self._upload_storage(
            body,
            path=storage_path,
            firebase_config=firebase_config,
            http_client=http_client,
        )
        evidence_url = url or f"/api/security/evidence/{event_id}.jpg"
        state.update({
            "eventId": event_id,
            "evidencePath": storage_path if url else str(local_snapshot),
            "evidenceUrl": evidence_url,
            "lastEventId": event_id,
            "lastEvidencePath": storage_path if url else str(local_snapshot),
            "lastEvidenceUrl": evidence_url,
            "lastEvidenceAt": metadata["createdAt"],
        })
        if alert:
            state["alertEventId"] = event_id
            state["alertEvidencePath"] = storage_path if url else str(local_snapshot)
            state["alertEvidenceUrl"] = evidence_url

    async def _upload_storage(
        self,
        body: bytes,
        *,
        path: str,
        firebase_config: dict[str, Any] | None,
        http_client: Any,
    ) -> str | None:
        config = firebase_config or {}
        configured_bucket = str(config.get("storageBucket") or os.getenv("FIREBASE_STORAGE_BUCKET") or "")
        project_id = str(config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
        bucket_candidates = [configured_bucket]
        if project_id:
            bucket_candidates.append(f"{project_id}.appspot.com")
        bucket_candidates = [b for i, b in enumerate(bucket_candidates) if b and b not in bucket_candidates[:i]]
        if not bucket_candidates or http_client is None:
            return None
        api_key = str(config.get("apiKey") or os.getenv("FIREBASE_API_KEY") or "")
        token = os.getenv("FIREBASE_STORAGE_BEARER_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or ""
        if not token:
            try:
                token = await asyncio.to_thread(_service_account_token)
            except Exception:
                token = ""
        headers = {"Content-Type": "image/jpeg"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for bucket in bucket_candidates:
            upload_url = (
                f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
                if token
                else f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o"
            )
            params = {"uploadType": "media", "name": path}
            if api_key and not token:
                params["key"] = api_key
            try:
                res = await http_client.post(upload_url, params=params, headers=headers, content=body, timeout=20)
                if res.status_code < 400:
                    return f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{quote(path, safe='')}?alt=media"
            except Exception:
                continue
        return None

    async def sync_firestore(
        self,
        public_state: dict[str, Any],
        *,
        firebase_config: dict[str, Any] | None,
        http_client: Any,
    ) -> None:
        config = firebase_config or {}
        project_id = str(config.get("projectId") or os.getenv("FIREBASE_PROJECT_ID") or "")
        if not project_id or http_client is None:
            return
        api_key = str(config.get("apiKey") or os.getenv("FIREBASE_API_KEY") or "")
        token = os.getenv("FIREBASE_FIRESTORE_BEARER_TOKEN") or os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN") or ""
        fields = {
            "armed": public_state.get("armed"),
            "presence": public_state.get("presence"),
            "alert": public_state.get("alert"),
            "lastPersonAt": public_state.get("lastPersonAt"),
            "lastMotionAt": public_state.get("lastMotionAt"),
            "lastSnapshotAt": public_state.get("lastSnapshotAt"),
            "personConfidence": public_state.get("personConfidence"),
            "motionScore": public_state.get("motionScore"),
            "evidencePath": public_state.get("evidencePath"),
            "evidenceUrl": public_state.get("evidenceUrl"),
            "eventId": public_state.get("eventId"),
            "lastEvidencePath": public_state.get("lastEvidencePath"),
            "lastEvidenceUrl": public_state.get("lastEvidenceUrl"),
            "lastEventId": public_state.get("lastEventId"),
            "cameraStale": public_state.get("cameraStale"),
            "lowLight": public_state.get("lowLight"),
            "modelStatus": public_state.get("modelStatus"),
            "updatedAt": _now(),
        }
        url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/ejdersted/security_garden"
        params: dict[str, str] = {}
        if api_key:
            params["key"] = api_key
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"fields": {k: _firestore_value(v) for k, v in fields.items()}}
        try:
            await http_client.patch(url, params=params, headers=headers, json=body, timeout=8)
        except Exception:
            pass

    def evidence_file(self, event_id: str) -> Path | None:
        if "/" in event_id or "\\" in event_id or ".." in event_id:
            return None
        path = self.evidence_dir / event_id / "snapshot.jpg"
        return path if path.is_file() else None

<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { initializeApp, getApp, getApps, type FirebaseOptions } from 'firebase/app';
  import { doc, getFirestore, onSnapshot, serverTimestamp, setDoc } from 'firebase/firestore';
  import Card from '$lib/Card.svelte';
  import { formatAirLine, type AirStatus } from '$lib/air';
  import { CAMERA_FACING_DOC, parseFacing, type CameraFacing } from '$lib/cameraFacing';
  import { store } from '$lib/ws.svelte';

  let videoEl = $state<HTMLVideoElement | null>(null);
  let stream = $state<MediaStream | null>(null);
  let cameraOn = $state(false);
  let voiceCaptureActive = $state(false);
  let error = $state('');
  // Phone in the hut films the garden. Both kiosk cards pick front/bag;
  // the choice lives in Firestore so Ejdersted can switch it.
  let facingMode = $state<CameraFacing>('environment');
  let facingReady = $state(false);
  let facingUnsub: (() => void) | null = null;
  let openingCamera = false;
  let publishStatus = $state('');
  let publisherChecked = $state(false);
  let canPublish = $state(false);
  let latestImageUrl = $state('/api/camera/latest.jpg');
  let latestAge = $state<number | null>(null);
  let latestAvailable = $state(false);
  let gardenUnreachable = $state(false);
  let viewerReady = $state(false);
  type PresenceStatus = {
    presence?: string;
    state?: string;
    label?: string;
    home?: boolean;
    alert?: boolean;
    lastPersonAt?: number | null;
    lastPersonAtIso?: string | null;
    lastPersonAge?: number | null;
    lastEvidenceAt?: number | null;
    lastEvidenceAtIso?: string | null;
    lastEvidenceUrl?: string;
    evidenceUrl?: string;
    motionScore?: number;
    personConfidence?: number;
    cameraStale?: boolean;
    lowLight?: boolean;
    modelStatus?: string;
  };
  let latestPresence = $state<PresenceStatus>({ presence: 'unknown', label: 'Ukendt' });
  let evidenceOpen = $state(false);
  let previewOpen = $state(false);
  let modalVideoEl = $state<HTMLVideoElement | null>(null);
  let snapshotTimer: ReturnType<typeof setTimeout> | null = null;
  let viewerTimer: ReturnType<typeof setInterval> | null = null;
  // Garden battery, read through the home hub's proxy. One line under the feed.
  type RemoteBattery = {
    online?: boolean;
    socPercent?: number | null;
    acOn?: boolean;
    dcOn?: boolean;
    solarWatts?: number | null;
    inWatts?: number | null;
    outWatts?: number | null;
  };
  let battery = $state<RemoteBattery | null>(null);
  let air = $state<AirStatus | null>(null);
  let batteryTimer: ReturnType<typeof setInterval> | null = null;
  const BATTERY_POLL_MS = 30_000;
  let publishing = false;
  let publisherRunning = false;
  let motionBaseline: Uint8ClampedArray | null = null;
  let motionPositiveFrames = 0;

  function motionFromVideo(): { score: number; motion: boolean } {
    if (!videoEl || !videoEl.videoWidth || !videoEl.videoHeight) return { score: 0, motion: false };
    const w = 160;
    const h = 90;
    const sample = document.createElement('canvas');
    sample.width = w;
    sample.height = h;
    const ctx = sample.getContext('2d', { willReadFrequently: true });
    if (!ctx) return { score: 0, motion: false };
    ctx.drawImage(videoEl, 0, 0, w, h);
    const rgba = ctx.getImageData(0, 0, w, h).data;
    const gray = new Uint8ClampedArray(w * h);
    for (let i = 0, j = 0; i < rgba.length; i += 4, j += 1) {
      gray[j] = Math.round(rgba[i] * 0.299 + rgba[i + 1] * 0.587 + rgba[i + 2] * 0.114);
    }
    const mean = gray.reduce((sum, value) => sum + value, 0) / gray.length;
    const normalized = new Uint8ClampedArray(gray.length);
    for (let i = 0; i < gray.length; i += 1) {
      normalized[i] = Math.max(0, Math.min(255, Math.round(gray[i] + (128 - mean))));
    }

    if (!motionBaseline) {
      motionBaseline = normalized;
      return { score: 0, motion: false };
    }

    let changed = 0;
    for (let idx = 0; idx < normalized.length; idx += 1) {
      const diff = Math.abs(normalized[idx] - motionBaseline[idx]);
      if (diff > 35) changed += 1;
      motionBaseline[idx] = Math.round(motionBaseline[idx] * 0.985 + normalized[idx] * 0.015);
    }
    const score = normalized.length ? changed / normalized.length : 0;
    motionPositiveFrames = score > 0.035 ? motionPositiveFrames + 1 : Math.max(0, motionPositiveFrames - 1);
    return { score, motion: motionPositiveFrames >= 2 };
  }

  function normalizePresence(input: any): PresenceStatus {
    if (!input || typeof input !== 'object') return { presence: 'unknown', label: 'Ukendt' };
    return {
      ...input,
      presence: input.presence ?? input.state ?? 'unknown',
      label: typeof input.label === 'string' ? input.label : 'Ukendt',
      home: !!input.home,
      alert: !!input.alert,
    };
  }

  function formatAge(seconds: number | null | undefined): string {
    if (seconds == null) return 'aldrig';
    if (seconds < 60) return `${Math.round(seconds)} sek siden`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min siden`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} t siden`;
    return `${Math.round(seconds / 86400)} dage siden`;
  }

  function formatEvidenceStamp(): string {
    const iso = latestPresence.lastEvidenceAtIso || latestPresence.lastPersonAtIso;
    const unix = latestPresence.lastEvidenceAt || latestPresence.lastPersonAt;
    const date = iso ? new Date(iso) : unix ? new Date(unix * 1000) : null;
    if (!date || Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('da-DK', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  // The kiosk posts every 2 s. Two minutes without a frame means it has left
  // Wi-Fi or died; the card then says so instead of ageing a stale frame.
  const KIOSK_STALE_S = 120;
  const kioskOffline = $derived(!canPublish && latestAvailable && (latestAge == null || latestAge > KIOSK_STALE_S));
  const gardenOffline = $derived(
    cameraMode() === 'viewer'
    && viewerReady
    && !canPublish
    && (gardenUnreachable || !latestAvailable || kioskOffline)
  );

  const headerStatus = () => {
    if (canPublish) return cameraOn ? 'live' : error ? 'fejl' : 'slukket';
    if (!latestAvailable) return 'venter';
    return kioskOffline ? 'kiosk offline' : 'kiosk live';
  };

  const evidenceUrl = () => latestPresence.lastEvidenceUrl || latestPresence.evidenceUrl || '';
  const presenceState = () => latestPresence.presence || latestPresence.state || 'unknown';
  const cameraMode = () => store.config.camera?.mode ?? (store.config.site === 'garden' ? 'publisher' : 'viewer');

  function canExpandPreview(): boolean {
    if (canPublish) return cameraOn && !!stream;
    return latestAvailable;
  }

  function togglePreview() {
    if (!canExpandPreview() && !previewOpen) return;
    previewOpen = !previewOpen;
    if (previewOpen) evidenceOpen = false;
  }

  function closePreview() {
    previewOpen = false;
  }

  async function publishSnapshot() {
    if (!videoEl || videoEl.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || publishing) return;
    const width = videoEl.videoWidth;
    const height = videoEl.videoHeight;
    if (!width || !height) return;

    publishing = true;
    try {
      const canvas = document.createElement('canvas');
      canvas.width = Math.min(width, 1280);
      canvas.height = Math.round((height / width) * canvas.width);
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.72));
      if (!blob) return;
      const motion = motionFromVideo();
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      const res = await fetch('/api/camera/snapshot', {
        method: 'POST',
        headers: {
          'Content-Type': 'image/jpeg',
          'X-Camera-Motion-Score': motion.score.toFixed(4),
          'X-Camera-Motion': motion.motion ? '1' : '0',
        },
        body: blob,
        signal: controller.signal,
      });
      clearTimeout(timeout);
      const data = await res.json().catch(() => null);
      latestPresence = normalizePresence(data?.presence ?? latestPresence);
      publishStatus = res.ok ? 'sender snapshots' : 'snapshot fejl';
    } catch {
      publishStatus = 'snapshot fejl';
    } finally {
      publishing = false;
    }
  }

  async function runSnapshotPublisher() {
    if (publisherRunning) return;
    publisherRunning = true;
    while (publisherRunning) {
      await publishSnapshot();
      await new Promise<void>((resolve) => {
        snapshotTimer = setTimeout(resolve, 2000);
      });
    }
  }

  function startSnapshotPublisher() {
    if (publisherRunning) return;
    void runSnapshotPublisher();
  }

  function stopSnapshotPublisher() {
    if (snapshotTimer) {
      clearTimeout(snapshotTimer);
      snapshotTimer = null;
    }
    publisherRunning = false;
    publishStatus = '';
    publishing = false;
  }

  async function checkPublisher() {
    try {
      const res = await fetch('/api/camera/publisher', { cache: 'no-store' });
      const data = await res.json();
      canPublish = !!data?.canPublish;
    } catch {
      canPublish = false;
    } finally {
      publisherChecked = true;
    }
  }

  async function refreshLatestSnapshot() {
    try {
      const res = await fetch('/api/camera/status', { cache: 'no-store' });
      const data = await res.json().catch(() => null);
      gardenUnreachable = !res.ok;
      latestAvailable = !!data?.available;
      latestAge = typeof data?.age === 'number' ? data.age : null;
      latestPresence = normalizePresence(data?.presence);
      if (latestAvailable) {
        latestImageUrl = `/api/camera/latest.jpg?t=${Date.now()}`;
      }
    } catch {
      gardenUnreachable = true;
      latestAvailable = false;
      latestAge = null;
      latestPresence = { presence: 'unknown', label: 'Ukendt' };
    } finally {
      viewerReady = true;
    }
  }

  async function retryGarden() {
    await Promise.all([refreshLatestSnapshot(), refreshBattery(), refreshAir()]);
  }

  async function refreshBattery() {
    try {
      const res = await fetch('/api/fossibot/status', { cache: 'no-store' });
      const data = await res.json();
      battery = data?.enabled === false ? null : data;
    } catch {
      battery = { online: false };
    }
  }

  async function refreshAir() {
    try {
      const res = await fetch('/api/air/status', { cache: 'no-store' });
      const data = (await res.json()) as AirStatus;
      air = data;
    } catch {
      air = { online: false };
    }
  }

  const airLine = $derived(formatAirLine(air));

  const batteryLine = $derived.by(() => {
    if (!battery) return '';
    if (!battery.online) return 'batteri · haven offline';
    const parts: string[] = [];
    if (typeof battery.socPercent === 'number') parts.push(`batteri ${Math.round(battery.socPercent)} %`);
    parts.push(battery.acOn ? '230 v tændt' : '230 v slukket');
    if (battery.dcOn) parts.push('12 v tændt');
    const incoming = battery.inWatts ?? battery.solarWatts;
    if ((incoming ?? 0) > 0) parts.push(`ind ${Math.round(incoming ?? 0)} w`);
    if ((battery.outWatts ?? 0) > 0) parts.push(`ud ${Math.round(battery.outWatts ?? 0)} w`);
    return parts.join(' · ');
  });

  function startViewer() {
    // Only the home kiosk needs this; the garden has its own battery card.
    if (cameraMode() === 'viewer' && !batteryTimer) {
      void refreshBattery();
      void refreshAir();
      batteryTimer = setInterval(() => {
        void refreshBattery();
        void refreshAir();
      }, BATTERY_POLL_MS);
    }
    if (viewerTimer) return;
    void refreshLatestSnapshot();
    viewerTimer = setInterval(() => void refreshLatestSnapshot(), 2000);
  }

  function stopViewer() {
    if (viewerTimer) {
      clearInterval(viewerTimer);
      viewerTimer = null;
    }
    if (batteryTimer) {
      clearInterval(batteryTimer);
      batteryTimer = null;
    }
  }

  async function openCamera() {
    if (!canPublish || openingCamera) return;
    openingCamera = true;
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    error = '';
    try {
      const video = {
        facingMode: { exact: facingMode },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      };
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      } catch {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode, width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        });
      }
      if (videoEl) {
        videoEl.srcObject = stream;
        await videoEl.play().catch(() => undefined);
      }
      cameraOn = true;
      startSnapshotPublisher();
    } catch (e: any) {
      error = `${e.name}: ${e.message}`;
      cameraOn = false;
      stopSnapshotPublisher();
    } finally {
      openingCamera = false;
    }
  }

  function stopCamera() {
    stopSnapshotPublisher();
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    if (videoEl) videoEl.srcObject = null;
    cameraOn = false;
    previewOpen = false;
  }

  function toggleCamera() {
    if (!canPublish) return;
    if (cameraOn) stopCamera();
    else openCamera();
  }

  async function writeFacing(next: CameraFacing) {
    try {
      const r = await fetch('/api/config/firebase');
      const cfg = (await r.json()) as Record<string, unknown>;
      if (!cfg.apiKey || typeof cfg.apiKey !== 'string') return;
      const app = getApps().length ? getApp() : initializeApp(cfg as FirebaseOptions);
      await setDoc(
        doc(getFirestore(app), 'ejdersted', CAMERA_FACING_DOC),
        { facing: next, updatedAt: serverTimestamp() },
        { merge: true },
      );
    } catch {
      /* rules or offline */
    }
  }

  function applyFacing(next: CameraFacing) {
    if (facingMode === next) {
      facingReady = true;
      return;
    }
    facingMode = next;
    facingReady = true;
    if (canPublish && cameraOn) void openCamera();
  }

  async function setFacing(next: CameraFacing) {
    if (facingMode === next) return;
    facingMode = next;
    void writeFacing(next);
    if (canPublish && cameraOn) void openCamera();
  }

  async function listenFacing() {
    try {
      const r = await fetch('/api/config/firebase');
      const cfg = (await r.json()) as Record<string, unknown>;
      if (!cfg.apiKey || typeof cfg.apiKey !== 'string') {
        facingReady = true;
        return;
      }
      const app = getApps().length ? getApp() : initializeApp(cfg as FirebaseOptions);
      const ref = doc(getFirestore(app), 'ejdersted', CAMERA_FACING_DOC);
      facingUnsub = onSnapshot(ref, (snap) => {
        const next = parseFacing(snap.data()?.facing) || 'environment';
        applyFacing(next);
      }, () => {
        facingReady = true;
      });
    } catch {
      facingReady = true;
    }
  }

  function handleVoiceCapture(event: Event) {
    const active = !!(event as CustomEvent<{ active?: boolean }>).detail?.active;
    voiceCaptureActive = active;
    if (active) stopCamera();
  }

  onMount(() => {
    window.addEventListener('hue:voice-capture', handleVoiceCapture);
    void listenFacing();
    return () => {
      window.removeEventListener('hue:voice-capture', handleVoiceCapture);
      facingUnsub?.();
      facingUnsub = null;
    };
  });

  $effect(() => {
    if (!previewOpen || !modalVideoEl || !stream) return;
    modalVideoEl.srcObject = stream;
    void modalVideoEl.play().catch(() => undefined);
  });

  $effect(() => {
    if (!previewOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePreview();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  $effect(() => {
    if (cameraMode() === 'publisher' && store.config.features.camera && !publisherChecked) {
      void checkPublisher();
    }
  });

  $effect(() => {
    if (cameraMode() === 'publisher' && store.config.features.camera && publisherChecked && facingReady && canPublish && !voiceCaptureActive && !cameraOn && !error) {
      void openCamera();
    }
  });

  $effect(() => {
    const viewer = cameraMode() === 'viewer'
      || (cameraMode() === 'publisher' && publisherChecked && !canPublish);
    if (viewer && store.config.features.camera) {
      stopCamera();
      startViewer();
    }
  });

  $effect(() => {
    const open = evidenceOpen || previewOpen;
    document.body.classList.toggle('camera-modal-open', open);
    return () => document.body.classList.remove('camera-modal-open');
  });

  onDestroy(() => {
    stopCamera();
    stopViewer();
  });
</script>

<Card
  name={gardenOffline ? '' : 'Kamera'}
  status={gardenOffline ? '' : headerStatus()}
  online={!gardenOffline && (cameraOn || (latestAvailable && !kioskOffline))}
>
  <div class="cam-stack" class:offline={gardenOffline}>
    {#if gardenOffline}
    <button type="button" class="garden-offline" onclick={retryGarden}>
      haven offline
    </button>
    {:else}
    <div class="camera-frame">
    <button
      type="button"
      class="camera-viewport"
      class:expandable={canExpandPreview()}
      class:stale={kioskOffline}
      aria-label={previewOpen ? 'Luk kamerabillede' : 'Vis kamerabillede stort'}
      onclick={togglePreview}
    >
      {#if canPublish}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video
          bind:this={videoEl}
          autoplay
          playsinline
          muted
        ></video>
      {:else if latestAvailable}
        <img src={latestImageUrl} alt="Seneste kamerabillede fra havekiosken" />
      {:else}
        <div class="camera-placeholder">venter på havekiosken</div>
      {/if}
    </button>
    {#if !kioskOffline}
      <div class="presence-bar">
        <div class="presence-left">
          {#if evidenceUrl()}
            <button
              type="button"
              class="presence-evidence"
              onclick={() => {
                evidenceOpen = true;
                previewOpen = false;
              }}
            >sidst hjemme</button>
          {/if}
        </div>
        <span
          class="presence-status"
          class:home={!!latestPresence.home}
          class:alert={!!latestPresence.alert}
          class:blind={presenceState() === 'camera_blind' || presenceState() === 'unknown'}
        >
          {presenceState() === 'checking' ? 'tjekker' : (latestPresence.label ?? 'Ukendt')}
        </span>
      </div>
    {/if}
    </div>
    <div class="cam-meta">
    {#if !gardenOffline}
      <div class="action-row">
        {#if canPublish}
          <button class="action-btn" onclick={toggleCamera}>
            {cameraOn ? 'stop' : 'start'}
          </button>
        {/if}
        <button
          type="button"
          class="action-btn"
          class:active={facingMode === 'user'}
          onclick={() => void setFacing('user')}
        >front</button>
        <button
          type="button"
          class="action-btn"
          class:active={facingMode === 'environment'}
          onclick={() => void setFacing('environment')}
        >bag</button>
      </div>
    {/if}
    {#if kioskOffline}
      <div class="publish-status">sidst set {formatAge(latestAge)}</div>
    {:else if latestAge != null}
      <div class="publish-status cam-age">havekiosk · {Math.round(latestAge)} s siden</div>
    {/if}
    {#if batteryLine}
      <div class="publish-status">{batteryLine}</div>
    {/if}
    {#if airLine}
      <div class="publish-status">{airLine}</div>
    {/if}
    {#if canPublish && publishStatus}
      <div class="publish-status">{publishStatus}</div>
    {/if}
    </div>
    {/if}
  </div>
</Card>

{#if previewOpen && canExpandPreview()}
  <div class="modal-backdrop preview-backdrop">
    <button type="button" class="preview-frame" aria-label="Luk kamerabillede" onclick={closePreview}>
      {#if canPublish && stream}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={modalVideoEl} autoplay playsinline muted></video>
      {:else}
        <img src={latestImageUrl} alt="Kamerabillede fra havekiosken" />
      {/if}
    </button>
  </div>
{/if}

{#if evidenceOpen && evidenceUrl()}
  <div class="modal-backdrop">
    <button class="modal-underlay" aria-label="Luk evidence" onclick={() => (evidenceOpen = false)}></button>
    <div class="evidence-modal" role="dialog" aria-modal="true">
      <div class="evidence-frame">
        <img src={evidenceUrl()} alt="Evidence fra seneste person-detektion" />
        {#if formatEvidenceStamp()}
          <div class="evidence-stamp">{formatEvidenceStamp()}</div>
        {/if}
      </div>
      <button type="button" class="modal-close" onclick={() => (evidenceOpen = false)}>luk</button>
    </div>
  </div>
{/if}

<style>
  .cam-stack {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    align-self: stretch;
    height: 100%;
    min-height: 0;
    width: 100%;
    gap: 10px;
  }

  .cam-stack.offline {
    width: 100%;
  }

  .garden-offline {
    appearance: none;
    border: 0;
    margin: 0;
    padding: 0;
    width: 100%;
    min-height: 160px;
    flex: 1;
    display: grid;
    place-items: center;
    background: transparent;
    color: rgba(255, 255, 255, 0.5);
    font: inherit;
    font-size: 0.72rem;
    letter-spacing: 0.14em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .garden-offline:active {
    color: rgba(255, 255, 255, 0.85);
  }

  .camera-frame {
    position: relative;
    width: 100%;
    max-width: 320px;
    border-radius: 16px;
    overflow: hidden;
    flex: 0 0 auto;
  }

  .cam-meta {
    width: 100%;
    max-width: 320px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    flex: 0 0 auto;
  }

  .camera-viewport {
    appearance: none;
    border: 0;
    padding: 0;
    width: 100%;
    max-width: none;
    aspect-ratio: 19 / 9;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 16px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.03);
    color: inherit;
    cursor: default;
  }

  .camera-viewport.expandable {
    cursor: zoom-in;
  }

  .camera-viewport.stale img {
    opacity: 0.4;
    filter: grayscale(1);
  }

  .camera-viewport video,
  .camera-viewport img,
  .preview-frame video,
  .preview-frame img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    pointer-events: none;
  }

  .preview-frame video,
  .preview-frame img {
    object-fit: contain;
  }

  .camera-placeholder {
    display: grid;
    place-items: center;
    width: 100%;
    height: 100%;
    color: rgba(255, 255, 255, 0.5);
    font-size: 0.7rem;
    letter-spacing: 0.12em;
    text-align: center;
    text-transform: uppercase;
  }

  .publish-status {
    max-width: 100%;
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    line-height: 1.45;
    text-align: center;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.45);
  }

  .presence-bar {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    display: grid;
    grid-template-columns: 1fr 1fr;
    align-items: baseline;
    column-gap: 12px;
    padding: 10px 8px 7px;
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.62));
    pointer-events: none;
  }

  .presence-left {
    min-width: 0;
    text-align: right;
  }

  .presence-evidence {
    appearance: none;
    border: 0;
    margin: 0;
    padding: 0;
    background: transparent;
    color: #9ee0ff;
    font: inherit;
    font-size: 0.58rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    pointer-events: auto;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.85);
  }

  .presence-evidence:active {
    color: #fff;
  }

  .presence-status {
    min-width: 0;
    text-align: left;
    font-size: 0.58rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.88);
  }

  .presence-status.home {
    color: #f2d27c;
  }

  .presence-status.alert {
    color: #ff8e7a;
  }

  .presence-status.blind {
    color: rgba(255, 255, 255, 0.88);
  }

  @media (orientation: landscape) and (max-height: 500px) {
    .cam-age {
      display: none;
    }
    .cam-meta {
      gap: 4px;
    }
    .presence-bar {
      column-gap: 8px;
      padding: 8px 6px 5px;
    }
    .presence-evidence,
    .presence-status {
      font-size: 0.52rem;
      letter-spacing: 0.06em;
    }
  }

  .modal-close {
    appearance: none;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.07);
    color: rgba(255, 255, 255, 0.86);
    cursor: pointer;
    font: inherit;
    font-size: 0.68rem;
    letter-spacing: 0.12em;
    padding: 8px 12px;
    text-transform: uppercase;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    display: grid;
    place-items: center;
    padding: 24px;
    background: rgba(0, 0, 0, 0.76);
  }

  .preview-backdrop {
    z-index: 50;
    padding: 3vh 2vw;
    background: rgba(0, 0, 0, 0.92);
  }

  .preview-frame {
    appearance: none;
    border: 0;
    padding: 0;
    width: min(96vw, 1600px);
    height: min(94vh, 96dvh);
    display: grid;
    place-items: center;
    background: #050505;
    border-radius: 18px;
    overflow: hidden;
    cursor: zoom-out;
  }

  .modal-underlay {
    position: absolute;
    inset: 0;
    z-index: 0;
    border: 0;
    background: transparent;
    cursor: default;
  }

  .evidence-modal {
    position: relative;
    z-index: 1;
    width: min(820px, 94vw);
    max-height: 88vh;
    padding: 18px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 24px;
    background: #090909;
  }

  .evidence-frame {
    position: relative;
    overflow: hidden;
    border-radius: 16px;
  }

  .evidence-modal img {
    width: 100%;
    max-height: 78vh;
    object-fit: contain;
    display: block;
    border-radius: 16px;
  }

  .evidence-stamp {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 14px 16px 12px;
    background: linear-gradient(transparent, rgba(0, 0, 0, 0.82));
    color: #f7f7f7;
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    pointer-events: none;
  }

  .modal-close {
    position: absolute;
    right: 24px;
    top: 24px;
    z-index: 2;
    background: rgba(0, 0, 0, 0.72);
    pointer-events: auto;
  }
</style>

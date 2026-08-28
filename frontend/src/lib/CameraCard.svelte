<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { store } from '$lib/ws.svelte';

  let videoEl = $state<HTMLVideoElement | null>(null);
  let stream = $state<MediaStream | null>(null);
  let cameraOn = $state(false);
  let voiceCaptureActive = $state(false);
  let error = $state('');
  // The mounted garden kiosk films the room with its screen-facing camera.
  // A page reload must not silently switch back to the obstructed rear camera.
  let facingMode = $state<'environment' | 'user'>('user');
  let publishStatus = $state('');
  let publisherChecked = $state(false);
  let canPublish = $state(false);
  let latestImageUrl = $state('/api/camera/latest.jpg');
  let latestAge = $state<number | null>(null);
  let latestAvailable = $state(false);
  type PresenceStatus = {
    presence?: string;
    state?: string;
    label?: string;
    home?: boolean;
    alert?: boolean;
    lastPersonAt?: number | null;
    lastPersonAtIso?: string | null;
    lastPersonAge?: number | null;
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
  let snapshotTimer: ReturnType<typeof setTimeout> | null = null;
  let viewerTimer: ReturnType<typeof setInterval> | null = null;
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

  const evidenceUrl = () => latestPresence.lastEvidenceUrl || latestPresence.evidenceUrl || '';
  const presenceState = () => latestPresence.presence || latestPresence.state || 'unknown';
  const cameraMode = () => store.config.camera?.mode ?? (store.config.site === 'garden' ? 'publisher' : 'viewer');

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
      const data = await res.json();
      latestAvailable = !!data?.available;
      latestAge = typeof data?.age === 'number' ? data.age : null;
      latestPresence = normalizePresence(data?.presence);
      if (latestAvailable) {
        latestImageUrl = `/api/camera/latest.jpg?t=${Date.now()}`;
      }
    } catch {
      latestAvailable = false;
      latestAge = null;
      latestPresence = { presence: 'unknown', label: 'Ukendt' };
    }
  }

  function startViewer() {
    if (viewerTimer) return;
    void refreshLatestSnapshot();
    viewerTimer = setInterval(() => void refreshLatestSnapshot(), 2000);
  }

  function stopViewer() {
    if (viewerTimer) {
      clearInterval(viewerTimer);
      viewerTimer = null;
    }
  }

  async function openCamera() {
    if (!canPublish) return;
    // Stop existing stream
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    error = '';
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
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
  }

  function toggleCamera() {
    if (!canPublish) return;
    if (cameraOn) stopCamera();
    else openCamera();
  }

  function flipCamera() {
    if (!canPublish) return;
    facingMode = facingMode === 'environment' ? 'user' : 'environment';
    if (cameraOn) openCamera();
  }

  function handleVoiceCapture(event: Event) {
    const active = !!(event as CustomEvent<{ active?: boolean }>).detail?.active;
    voiceCaptureActive = active;
    if (active) stopCamera();
  }

  onMount(() => {
    window.addEventListener('hue:voice-capture', handleVoiceCapture);
    return () => window.removeEventListener('hue:voice-capture', handleVoiceCapture);
  });

  $effect(() => {
    if (cameraMode() === 'publisher' && store.config.features.camera && !publisherChecked) {
      void checkPublisher();
    }
  });

  $effect(() => {
    if (cameraMode() === 'publisher' && store.config.features.camera && publisherChecked && canPublish && !voiceCaptureActive && !cameraOn && !error) {
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

  onDestroy(() => {
    stopCamera();
    stopViewer();
  });
</script>

<Card name="Kamera" status={canPublish ? (cameraOn ? 'live' : error ? 'fejl' : 'slukket') : latestAvailable ? 'kiosk live' : 'venter'} online={cameraOn || latestAvailable}>
  <div class="cam-stack">
    <div class="camera-viewport">
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
    </div>
    {#if canPublish}
      <div class="action-row">
        <button class="action-btn" onclick={toggleCamera}>
          {cameraOn ? 'stop' : 'start'}
        </button>
        <button class="action-btn" onclick={flipCamera} disabled={!cameraOn}>
          {facingMode === 'environment' ? 'front' : 'bag'}
        </button>
      </div>
    {:else if latestAge != null}
      <div class="publish-status">havekiosk · {Math.round(latestAge)}s siden</div>
    {/if}
    <div
      class="presence-status"
      class:home={!!latestPresence.home}
      class:alert={!!latestPresence.alert}
      class:blind={presenceState() === 'camera_blind' || presenceState() === 'unknown'}
    >
      {latestPresence.label ?? 'Ukendt'}
    </div>
    <div class="presence-detail">
      Sidst hjemme: {formatAge(latestPresence.lastPersonAge)}
    </div>
    {#if evidenceUrl()}
      <button class="evidence-link" onclick={() => (evidenceOpen = true)}>vis billede</button>
    {/if}
    {#if canPublish && publishStatus}
      <div class="publish-status">{publishStatus}</div>
    {/if}
  </div>
</Card>

{#if evidenceOpen && evidenceUrl()}
  <div class="modal-backdrop">
    <button class="modal-underlay" aria-label="Luk evidence" onclick={() => (evidenceOpen = false)}></button>
    <div class="evidence-modal" role="dialog" aria-modal="true">
      <button class="modal-close" onclick={() => (evidenceOpen = false)}>luk</button>
      <img src={evidenceUrl()} alt="Evidence fra seneste person-detektion" />
    </div>
  </div>
{/if}

<style>
  .cam-stack {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    align-self: center;
  }

  .camera-viewport {
    width: 100%;
    max-width: 320px;
    aspect-ratio: 19 / 9;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 16px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.03);
  }

  video,
  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
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
    font-size: 0.68rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.45);
  }

  .presence-status {
    font-size: 0.72rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.58);
  }

  .presence-status.home {
    color: #f2d27c;
  }

  .presence-status.alert {
    color: #ff8e7a;
  }

  .presence-status.blind {
    color: rgba(255, 255, 255, 0.74);
  }

  .presence-detail {
    margin-top: -8px;
    color: rgba(255, 255, 255, 0.54);
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .evidence-link,
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

  .modal-underlay {
    position: absolute;
    inset: 0;
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

  .evidence-modal img {
    width: 100%;
    max-height: 78vh;
    object-fit: contain;
    border-radius: 16px;
  }

  .modal-close {
    position: absolute;
    right: 24px;
    top: 24px;
    background: rgba(0, 0, 0, 0.72);
  }
</style>

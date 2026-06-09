<script lang="ts">
  import { onDestroy } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { store } from '$lib/ws.svelte';

  let videoEl = $state<HTMLVideoElement | null>(null);
  let stream = $state<MediaStream | null>(null);
  let cameraOn = $state(false);
  let error = $state('');
  let facingMode = $state<'environment' | 'user'>('environment');
  let publishStatus = $state('');
  let snapshotTimer: ReturnType<typeof setTimeout> | null = null;
  let publishing = false;
  let publisherRunning = false;

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
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      const res = await fetch('/api/camera/snapshot', {
        method: 'POST',
        headers: { 'Content-Type': 'image/jpeg' },
        body: blob,
        signal: controller.signal,
      });
      clearTimeout(timeout);
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

  async function openCamera() {
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
    if (cameraOn) stopCamera();
    else openCamera();
  }

  function flipCamera() {
    facingMode = facingMode === 'environment' ? 'user' : 'environment';
    if (cameraOn) openCamera();
  }

  $effect(() => {
    if (store.config.site === 'garden' && store.config.features.camera && !cameraOn && !error) {
      void openCamera();
    }
  });

  onDestroy(() => stopCamera());
</script>

<Card name="Kamera" status={cameraOn ? 'live' : error ? 'fejl' : 'slukket'} online={cameraOn}>
  <div class="cam-stack">
    <div class="camera-viewport">
      <!-- svelte-ignore a11y_media_has_caption -->
      <video
        bind:this={videoEl}
        autoplay
        playsinline
        muted
      ></video>
    </div>
    <div class="action-row">
      <button class="action-btn" onclick={toggleCamera}>
        {cameraOn ? 'stop' : 'start'}
      </button>
      <button class="action-btn" onclick={flipCamera} disabled={!cameraOn}>
        {facingMode === 'environment' ? 'front' : 'bag'}
      </button>
    </div>
    {#if publishStatus}
      <div class="publish-status">{publishStatus}</div>
    {/if}
  </div>
</Card>

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

  video {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .publish-status {
    font-size: 0.68rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: rgba(255, 255, 255, 0.45);
  }
</style>

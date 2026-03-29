<script lang="ts">
  import { onDestroy } from 'svelte';
  import Card from '$lib/Card.svelte';

  let videoEl = $state<HTMLVideoElement | null>(null);
  let stream = $state<MediaStream | null>(null);
  let cameraOn = $state(false);
  let error = $state('');
  let facingMode = $state<'environment' | 'user'>('environment');

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
      if (videoEl) videoEl.srcObject = stream;
      cameraOn = true;
    } catch (e: any) {
      error = `${e.name}: ${e.message}`;
      cameraOn = false;
    }
  }

  function stopCamera() {
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

  onDestroy(() => stopCamera());
</script>

<Card name="Kamera" status={cameraOn ? 'live' : error ? 'fejl' : 'slukket'} online={cameraOn}>
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
</Card>

<style>
  .camera-viewport {
    width: 240px;
    height: 240px;
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
</style>

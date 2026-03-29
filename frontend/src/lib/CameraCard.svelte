<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

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

<div class="camera-card">
  <div class="camera-top">
    <span class="camera-label">KAMERA</span>
    <span class="camera-status" class:active={cameraOn}>
      {cameraOn ? 'live' : error ? 'fejl' : 'slukket'}
    </span>
  </div>

  <div class="camera-viewport">
    <!-- svelte-ignore a11y_media_has_caption -->
    <video
      bind:this={videoEl}
      autoplay
      playsinline
      muted
    ></video>
    {#if error}
      <span class="camera-error">{error}</span>
    {:else if !cameraOn}
      <span class="camera-placeholder">slukket</span>
    {/if}
  </div>

  <div class="camera-controls">
    <button class="cam-btn" class:active={cameraOn} onclick={toggleCamera}>
      {cameraOn ? 'stop' : 'start'}
    </button>
    <button class="cam-btn" onclick={flipCamera} disabled={!cameraOn}>
      {facingMode === 'environment' ? 'front' : 'bag'}
    </button>
  </div>
</div>

<style>
  .camera-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    width: 100%;
    gap: 16px;
  }

  .camera-top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    width: 240px;
  }

  .camera-label {
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #888;
  }

  .camera-status {
    font-size: 0.6rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #888;
    transition: color 0.3s;
  }
  .camera-status.active { color: #0080c8; }

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

  .camera-placeholder, .camera-error {
    font-size: 0.7rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #888;
  }

  .camera-controls {
    display: flex;
    gap: 12px;
    width: 240px;
    justify-content: center;
  }

  .cam-btn {
    flex: 1;
    padding: 12px 0;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    background: none;
    color: #ebebeb;
    font-size: 0.75rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
    transition: border-color 0.2s, color 0.2s, opacity 0.2s;
    -webkit-tap-highlight-color: transparent;
  }
  .cam-btn.active {
    border-color: rgba(0, 128, 200, 0.4);
    color: #0080c8;
  }
  .cam-btn:disabled {
    opacity: 0.3;
    cursor: default;
  }
</style>

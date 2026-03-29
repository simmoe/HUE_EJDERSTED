<script lang="ts">
  import { onMount } from 'svelte';

  let videoEl = $state<HTMLVideoElement | null>(null);
  let stream = $state<MediaStream | null>(null);
  let cameraOn = $state(false);
  let error = $state('');

  async function startCamera() {
    if (stream) return;
    error = '';
    console.log('[Camera] requesting getUserMedia...');
    console.log('[Camera] navigator.mediaDevices:', !!navigator.mediaDevices);
    console.log('[Camera] getUserMedia:', !!navigator.mediaDevices?.getUserMedia);
    try {
      const constraints = {
        video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      };
      console.log('[Camera] constraints:', JSON.stringify(constraints));
      stream = await navigator.mediaDevices.getUserMedia(constraints);
      console.log('[Camera] got stream, tracks:', stream.getTracks().map(t => `${t.kind}:${t.label}:${t.readyState}`));
      if (videoEl) {
        videoEl.srcObject = stream;
        console.log('[Camera] assigned to video element');
      } else {
        console.warn('[Camera] videoEl is null!');
      }
      cameraOn = true;
    } catch (e: any) {
      console.error('[Camera] error:', e.name, e.message);
      error = `${e.name}: ${e.message}`;
    }
  }

  onMount(() => {
    console.log('[Camera] onMount — starting camera');
    startCamera();
  });
</script>

<div class="camera-card">
  <div class="camera-top">
    <span class="camera-label">KAMERA</span>
    <span class="camera-status" class:active={cameraOn}>
      {cameraOn ? 'live' : error ? 'fejl' : 'standby'}
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
      <span class="camera-placeholder">venter...</span>
    {/if}
  </div>
</div>

<style>
  .camera-card {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
  }

  .camera-top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .camera-label {
    font-size: 0.65rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #595959;
  }

  .camera-status {
    font-size: 0.6rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #595959;
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
    background: rgba(255, 255, 255, 0.02);
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
    color: #595959;
  }
</style>

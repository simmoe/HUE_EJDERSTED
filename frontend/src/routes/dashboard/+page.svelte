<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  type CameraStatus = {
    ok: boolean;
    available: boolean;
    ts: number | null;
    age: number | null;
    bytes: number;
    error?: string;
  };

  let status = $state<CameraStatus | null>(null);
  let imageUrl = $state('/api/camera/latest.jpg');
  let actionStatus = $state('');
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const ageLabel = () => {
    if (!status?.available || status.age == null) return 'venter på kamera';
    if (status.age < 1) return 'lige nu';
    return `${Math.round(status.age)} sek siden`;
  };

  async function refreshCamera() {
    try {
      const res = await fetch('/api/camera/status', { cache: 'no-store' });
      status = await res.json();
      if (status?.available) {
        imageUrl = `/api/camera/latest.jpg?t=${Date.now()}`;
      }
    } catch {
      status = { ok: false, available: false, ts: null, age: null, bytes: 0, error: 'offline' };
    }
  }

  async function callAction(label: string, input: RequestInfo | URL, init?: RequestInit) {
    actionStatus = `${label}...`;
    try {
      const res = await fetch(input, init);
      actionStatus = res.ok ? `${label}: ok` : `${label}: fejl`;
    } catch {
      actionStatus = `${label}: offline`;
    }
  }

  onMount(() => {
    void refreshCamera();
    pollTimer = setInterval(() => void refreshCamera(), 2000);
  });

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
  });
</script>

<svelte:head>
  <title>Garden Dashboard</title>
</svelte:head>

<main>
  <section class="hero">
    <p class="eyebrow">Garden dashboard</p>
    <h1>Haven</h1>
    <p class="status" class:fresh={!!status?.available && (status.age ?? 999) < 5}>
      Kamera: {ageLabel()}
    </p>
  </section>

  <section class="camera-card">
    {#if status?.available}
      <img src={imageUrl} alt="Seneste kamerabillede fra haven" />
    {:else}
      <div class="placeholder">Venter på første snapshot fra kiosken</div>
    {/if}
  </section>

  <section class="controls">
    <button onclick={() => callAction('Kiosk', '/api/kiosk', { method: 'POST' })}>Reapply kiosk</button>
    <button onclick={() => callAction('Lysstyrke', '/api/brightness/255', { method: 'PUT' })}>Brightness max</button>
    <button onclick={() => void refreshCamera()}>Refresh</button>
  </section>

  {#if actionStatus}
    <p class="action-status">{actionStatus}</p>
  {/if}
</main>

<style>
  :global(body) {
    margin: 0;
    min-height: 100vh;
    background: #050505;
    color: #f2f2f2;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }

  main {
    width: min(980px, calc(100vw - 32px));
    margin: 0 auto;
    padding: 32px 0;
  }

  .hero {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 24px;
  }

  .eyebrow,
  .status,
  .action-status {
    margin: 0;
    color: rgba(255, 255, 255, 0.55);
    font-size: 0.8rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  h1 {
    margin: 0;
    font-size: clamp(2.6rem, 8vw, 5.5rem);
    font-weight: 250;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .fresh {
    color: #9cf2b8;
  }

  .camera-card {
    display: grid;
    place-items: center;
    min-height: 55vh;
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 28px;
    background: rgba(255, 255, 255, 0.035);
  }

  img {
    display: block;
    width: 100%;
    height: 100%;
    max-height: 70vh;
    object-fit: contain;
  }

  .placeholder {
    color: rgba(255, 255, 255, 0.4);
    font-size: 0.9rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 18px;
  }

  button {
    appearance: none;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.06);
    color: #f2f2f2;
    cursor: pointer;
    font: inherit;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    padding: 12px 16px;
    text-transform: uppercase;
  }

  button:active {
    transform: translateY(1px);
  }

  .action-status {
    margin-top: 14px;
  }
</style>

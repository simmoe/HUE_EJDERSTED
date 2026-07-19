<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  type CameraStatus = {
    ok: boolean;
    available: boolean;
    ts: number | null;
    age: number | null;
    bytes: number;
    presence?: SecurityStatus;
    error?: string;
  };

  type SecurityStatus = {
    presence?: 'empty' | 'checking' | 'home' | 'unknown' | 'camera_blind' | string;
    state?: string;
    label?: string;
    home?: boolean;
    armed?: boolean;
    alert?: boolean;
    lastPersonAge?: number | null;
    lastPersonAtIso?: string | null;
    lastEvidenceUrl?: string;
    evidenceUrl?: string;
    personConfidence?: number;
    motionScore?: number;
    modelStatus?: string;
    cameraStale?: boolean;
    lowLight?: boolean;
  };

  let status = $state<CameraStatus | null>(null);
  let security = $state<SecurityStatus | null>(null);
  let imageUrl = $state('/api/camera/latest.jpg');
  let actionStatus = $state('');
  let evidenceOpen = $state(false);
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  const ageLabel = () => {
    if (!status?.available || status.age == null) return 'venter på kamera';
    if (status.age < 1) return 'lige nu';
    return `${Math.round(status.age)} sek siden`;
  };

  function normalizeSecurity(input: any): SecurityStatus | null {
    if (!input || typeof input !== 'object') return null;
    return {
      ...input,
      presence: input.presence ?? input.state ?? 'unknown',
      label: typeof input.label === 'string' ? input.label : 'Ukendt',
      home: !!input.home,
      armed: !!input.armed,
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

  const evidenceUrl = () => security?.lastEvidenceUrl || security?.evidenceUrl || '';

  async function refreshCamera() {
    try {
      const res = await fetch('/api/camera/status', { cache: 'no-store' });
      status = await res.json();
      security = normalizeSecurity(status?.presence);
      if (status?.available) {
        imageUrl = `/api/camera/latest.jpg?t=${Date.now()}`;
      }
    } catch {
      status = { ok: false, available: false, ts: null, age: null, bytes: 0, error: 'offline' };
      security = null;
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

  async function setArmed(armed: boolean) {
    actionStatus = armed ? 'Alarm tilkobles...' : 'Alarm frakobles...';
    try {
      const res = await fetch('/api/security/garden/armed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ armed }),
      });
      const data = await res.json().catch(() => null);
      security = normalizeSecurity(data?.security) ?? security;
      actionStatus = res.ok ? (armed ? 'Alarm: tilkoblet' : 'Alarm: frakoblet') : 'Alarm: fejl';
    } catch {
      actionStatus = 'Alarm: offline';
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

  <section
    class="security-panel"
    class:home={!!security?.home}
    class:alert={!!security?.alert}
    class:blind={security?.presence === 'camera_blind' || security?.presence === 'unknown'}
  >
    <p class="presence">{security?.label ?? 'Ukendt'}</p>
    <p class="security-detail">Sidst hjemme: {formatAge(security?.lastPersonAge)}</p>
    <p class="security-detail">
      Model: {security?.modelStatus ?? 'ukendt'} · motion {Math.round((security?.motionScore ?? 0) * 100)}%
      {#if security?.personConfidence != null}
        · person {Math.round(security.personConfidence * 100)}%
      {/if}
    </p>
    <div class="security-actions">
      <button onclick={() => setArmed(!security?.armed)}>
        {security?.armed ? 'Frakobl alarm' : 'Tilkobl alarm'}
      </button>
      {#if evidenceUrl()}
        <button onclick={() => (evidenceOpen = true)}>Vis evidence</button>
      {/if}
    </div>
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

{#if evidenceOpen && evidenceUrl()}
  <div class="modal-backdrop">
    <button class="modal-underlay" aria-label="Luk evidence" onclick={() => (evidenceOpen = false)}></button>
    <div class="evidence-modal" role="dialog" aria-modal="true">
      <button class="modal-close" onclick={() => (evidenceOpen = false)}>Luk</button>
      <img src={evidenceUrl()} alt="Evidence fra seneste person-detektion" />
    </div>
  </div>
{/if}

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

  .security-panel {
    margin-top: 14px;
    padding: 18px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 22px;
    background: rgba(255, 255, 255, 0.045);
  }

  .presence {
    margin: 0 0 8px;
    color: rgba(255, 255, 255, 0.58);
    font-size: 0.85rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
  }

  .security-panel.home .presence {
    color: #f2d27c;
  }

  .security-panel.alert .presence {
    color: #ff8e7a;
  }

  .security-panel.blind .presence {
    color: rgba(255, 255, 255, 0.78);
  }

  .security-detail {
    margin: 0 0 6px;
    color: rgba(255, 255, 255, 0.58);
    font-size: 0.82rem;
  }

  .security-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
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

  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 20;
    display: grid;
    place-items: center;
    padding: 24px;
    background: rgba(0, 0, 0, 0.78);
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
    width: min(960px, 94vw);
    max-height: 88vh;
    padding: 18px;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 24px;
    background: #090909;
  }

  .evidence-modal img {
    display: block;
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

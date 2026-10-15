<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { store, type GateMode, type GateScene } from '$lib/ws.svelte';

  let busy = $state(false);

  const scene = $derived(store.gate.scene);
  const scenes = $derived(store.gate.scenes > 0 ? store.gate.scenes : 3);
  const mode = $derived(
    store.gate.mode === 'on' || store.gate.mode === 'auto' || store.gate.mode === 'off'
      ? store.gate.mode
      : 'auto',
  );
  const online = $derived(store.gate.online === true);
  const status = $derived(online ? '' : 'offline');

  function apply(data: Partial<GateScene>) {
    store.gate = {
      scene: typeof data.scene === 'number' ? data.scene : store.gate.scene,
      scenes: typeof data.scenes === 'number' && data.scenes > 0 ? data.scenes : store.gate.scenes,
      mode: data.mode ?? store.gate.mode,
      on: typeof data.on === 'boolean' ? data.on : store.gate.on,
      online: typeof data.online === 'boolean' ? data.online : store.gate.online,
      status: data.online === false ? 'offline' : '',
      ip: typeof data.ip === 'string' ? data.ip : store.gate.ip,
    };
  }

  onMount(() => {
    fetch('/api/gate/scene')
      .then((r) => r.json())
      .then((data) => apply(data))
      .catch(() => {});
  });

  async function next() {
    if (busy) return;
    const upcoming = scene >= 1 ? (scene % scenes) + 1 : 1;
    busy = true;
    try {
      const r = await fetch('/api/gate/scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene: upcoming }),
      });
      const data = await r.json();
      if (r.ok) apply(data);
    } catch {
      /* MQTT/offline is already the card status */
    } finally {
      busy = false;
    }
  }

  function setMode(nextMode: GateMode) {
    store.setGateMode(nextMode);
  }
</script>

<Card name="Låge" {status} online={online && !!store.gate.on}>
  <div class="gate">
    <button type="button" class="scene" onclick={next} disabled={busy} aria-label={busy ? 'Skifter' : `Cyklus ${scene || '–'}`}>
      {#if busy}· · ·{:else}{scene || '·'}{/if}
    </button>
    <div class="gate-modes" role="group" aria-label="Låge-styring">
      <button type="button" class="action-btn" class:active={mode === 'on'} onclick={() => setMode('on')}>tænd</button>
      <button type="button" class="action-btn" class:active={mode === 'auto'} onclick={() => setMode('auto')}>auto</button>
      <button type="button" class="action-btn" class:active={mode === 'off'} onclick={() => setMode('off')}>sluk</button>
    </div>
  </div>
</Card>

<style>
  .gate {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-evenly;
    gap: 14px;
    padding: 14px 6px;
  }

  /* Same 200px cap as the lamp dial. Viewport units, because a height of
     100% of the card stretches the circle on the Android kiosk. The digit
     is 28% of the diameter, the same share as the number in VolumeKnob. */
  .scene {
    width: min(42vw, 24vh, 168px);
    height: min(42vw, 24vh, 168px);
    display: grid;
    place-items: center;
    padding: 0;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.42);
    background: none;
    color: var(--light);
    font: inherit;
    font-weight: 200;
    font-size: calc(min(42vw, 24vh, 168px) * 0.28);
    line-height: 1;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .scene:disabled {
    cursor: default;
    animation: pulse-dim 1.2s ease-in-out infinite;
  }

  .scene:active:not(:disabled) {
    background: rgba(255, 255, 255, 0.08);
  }

  .gate-modes {
    display: flex;
    gap: 6px;
  }
</style>

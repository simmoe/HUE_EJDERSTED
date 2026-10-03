<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/Card.svelte';
  import { store } from '$lib/ws.svelte';

  let busy = $state(false);
  let note = $state('');
  let seen = store.gate.scene;

  const scene = $derived(store.gate.scene);
  const scenes = $derived(store.gate.scenes > 0 ? store.gate.scenes : 4);
  const status = $derived(busy ? '' : note || store.gate.status);

  $effect(() => {
    if (store.gate.scene === seen) return;
    seen = store.gate.scene;
    note = '';
  });

  function apply(data: { scene?: number; scenes?: number; status?: string; ip?: string }) {
    store.gate = {
      scene: typeof data.scene === 'number' ? data.scene : store.gate.scene,
      scenes: typeof data.scenes === 'number' && data.scenes > 0 ? data.scenes : store.gate.scenes,
      status: data.status === 'slukket' || data.status === 'mqtt' ? data.status : '',
      ip: typeof data.ip === 'string' ? data.ip : '',
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
    const upcoming = (scene % scenes) + 1;
    busy = true;
    note = '';
    try {
      const r = await fetch('/api/gate/scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene: upcoming }),
      });
      const data = await r.json();
      if (!r.ok) {
        note = data.status === 'slukket' ? 'slukket' : 'mqtt';
        return;
      }
      apply(data);
    } catch {
      note = 'mqtt';
    } finally {
      busy = false;
    }
  }
</script>

<Card name="Låge" {status}>
  <div class="gate">
    <button type="button" class="scene" onclick={next} disabled={busy} aria-label={busy ? 'Skifter' : `Scene ${scene}`}>
      {#if busy}· · ·{:else}{scene}{/if}
    </button>
  </div>
</Card>

<style>
  .gate {
    height: 100%;
    min-height: 0;
    display: grid;
    place-items: center;
  }

  /* Same 200px cap as the lamp dial. Viewport units, because a height of
     100% of the card stretches the circle on the Android kiosk. The digit
     is 28% of the diameter, the same share as the number in VolumeKnob. */
  .scene {
    width: min(42vw, 28vh, 200px);
    height: min(42vw, 28vh, 200px);
    display: grid;
    place-items: center;
    padding: 0;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.42);
    background: none;
    color: var(--light);
    font: inherit;
    font-weight: 200;
    font-size: calc(min(42vw, 28vh, 200px) * 0.28);
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
</style>

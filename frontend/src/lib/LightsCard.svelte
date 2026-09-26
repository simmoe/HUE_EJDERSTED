<script lang="ts">
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import { store, type GardenLight } from '$lib/ws.svelte';

  // One lamp per card. The LYS page's own card-arrow reads the next card's
  // name, so the lamp name lives only in the card header.
  const seng = $derived(store.lights.find((l) => l.id === 'seng'));
  const toilet = $derived(store.lights.find((l) => l.id === 'toilet'));

  // The knob already shows on/off; the header only says what the control
  // cannot — that the lamp is unreachable.
  function lampStatus(light: GardenLight | undefined): string {
    if (!light) return '';
    return light.online ? '' : 'offline';
  }

  function toggle(light: GardenLight | undefined, onLevel: number) {
    if (!light) return;
    store.setLightBrightness(light.id, light.on ? 0 : Math.max(light.brightness, onLevel));
  }
</script>

{#if seng}
  <Card name="Seng" status={lampStatus(seng)} online={!!seng.online && !!seng.on}>
    <div class="dial">
      <div class="dial-box">
        <VolumeKnob
          value={seng.brightness}
          muted={!seng.on}
          disabled={!seng.online}
          onchange={(v) => store.setLightBrightness(seng.id, v)}
          onmute={() => toggle(seng, 55)}
        />
      </div>
    </div>
  </Card>
{/if}

{#if toilet}
  <Card name="Toilet" status={lampStatus(toilet)} online={!!toilet.online && !!toilet.on}>
    <div class="dial">
      <div class="dial-box">
        <VolumeKnob
          value={toilet.brightness}
          muted={!toilet.on}
          disabled={!toilet.online}
          onchange={(v) => store.setLightBrightness(toilet.id, v)}
          onmute={() => toggle(toilet, 100)}
        />
      </div>
    </div>
  </Card>
{/if}

<style>
  /* Seng / Toilet: same dial as the Hue rooms at home. */
  .dial {
    height: 100%;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .dial-box {
    height: 100%;
    max-height: 200px;
    aspect-ratio: 1;
    max-width: 100%;
  }
</style>

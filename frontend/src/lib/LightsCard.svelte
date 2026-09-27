<script lang="ts">
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import { store, type GardenLight } from '$lib/ws.svelte';

  // One lamp per card. The LYS page's own card-arrow reads the next card's
  // name, so the lamp name lives only in the card header.
  const LAMPS = [
    { id: 'loft', name: 'Loft', onLevel: 100 },
    { id: 'seng', name: 'Seng', onLevel: 55 },
    { id: 'toilet', name: 'Toilet', onLevel: 100 },
  ] as const;
  const lamps = $derived(
    LAMPS.map((meta) => ({ ...meta, light: store.lights.find((l) => l.id === meta.id) })).filter(
      (row): row is (typeof LAMPS)[number] & { light: GardenLight } => !!row.light,
    ),
  );

  // The knob already shows on/off; the header only says what the control
  // cannot — that the lamp is unreachable.
  function lampStatus(light: GardenLight): string {
    return light.online ? '' : 'offline';
  }

  function toggle(light: GardenLight, onLevel: number) {
    store.setLightBrightness(light.id, light.on ? 0 : Math.max(light.brightness, onLevel));
  }
</script>

{#each lamps as row (row.id)}
  <Card name={row.name} status={lampStatus(row.light)} online={!!row.light.online && !!row.light.on}>
    <div class="dial">
      <div class="dial-box">
        <VolumeKnob
          value={row.light.brightness}
          muted={!row.light.on}
          disabled={!row.light.online}
          onchange={(v) => store.setLightBrightness(row.light.id, v)}
          onmute={() => toggle(row.light, row.onLevel)}
        />
      </div>
    </div>
  </Card>
{/each}

<style>
  /* Garden lamps: same dial as the Hue rooms at home. */
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

<script lang="ts">
  import Card from '$lib/Card.svelte';
  import VolumeKnob from '$lib/VolumeKnob.svelte';
  import { store, type GardenLight } from '$lib/ws.svelte';

  // One lamp per card. The LYS page's own card-arrow reads the next card's
  // name, so the lamp name lives only in the card header.
  const flare = $derived(
    store.lights.find((l) => l.protocol !== 'zigbee' && l.protocol !== 'ikea')
  );
  const seng = $derived(store.lights.find((l) => l.id === 'seng'));
  const toilet = $derived(store.lights.find((l) => l.id === 'toilet'));

  type YardMode = 'off' | 'dæmpet' | 'kraftig' | 'fest';
  const SCENES: { id: Exclude<YardMode, 'off'>; label: string }[] = [
    { id: 'dæmpet', label: 'dæmpet' },
    { id: 'kraftig', label: 'kraftig' },
    { id: 'fest', label: 'fest' },
  ];

  function yardMode(light: GardenLight | undefined): YardMode {
    if (!light?.on) return 'off';
    if (light.scene === 'fest') return 'fest';
    if (light.scene === 'dæmpet' || light.scene === 'daempet') return 'dæmpet';
    if (light.scene === 'kraftig' || light.scene === 'hygge') return 'kraftig';
    if (light.mode === 'colour') return 'fest';
    return (light.brightness ?? 0) <= 40 ? 'dæmpet' : 'kraftig';
  }

  const yard = $derived(yardMode(flare));

  function yardTap(scene: Exclude<YardMode, 'off'>) {
    if (!flare) return;
    // Tapping the lit scene again turns the lamp off.
    store.setLightScene(flare.id, yard === scene ? 'off' : scene);
  }

  // The lit tile or the knob already shows on/off; the header only says
  // what the control cannot — that the lamp is unreachable.
  function lampStatus(light: GardenLight | undefined): string {
    if (!light) return '';
    return light.online ? '' : 'offline';
  }

  // Tapping the Gård status reconnects the Flare (it drops off Wi-Fi when
  // 230 V has been cut).
  let connecting = $state(false);
  const yardStatus = $derived(connecting ? 'forbinder…' : lampStatus(flare));

  async function yardReconnect() {
    if (!flare || flare.online || connecting) return;
    connecting = true;
    try {
      await store.connectLight(flare.id);
    } catch {
      // status falls back to whatever the store says
    } finally {
      connecting = false;
    }
  }

  function toggle(light: GardenLight | undefined, onLevel: number) {
    if (!light) return;
    store.setLightBrightness(light.id, light.on ? 0 : Math.max(light.brightness, onLevel));
  }
</script>

{#if flare}
  <Card name="Gård" status={yardStatus} online={!!flare.online && !!flare.on} onstatus={yardReconnect}>
    <div class="scenes" role="group" aria-label="Gårdlys">
      {#each SCENES as scene (scene.id)}
        <button
          type="button"
          class="scene"
          class:active={yard === scene.id}
          disabled={!flare.online}
          aria-pressed={yard === scene.id}
          onclick={() => yardTap(scene.id)}
        >
          <svg class="mark" viewBox="0 0 40 40" aria-hidden="true">
            {#if scene.id === 'dæmpet'}
              <circle cx="20" cy="20" r="4" />
            {:else if scene.id === 'kraftig'}
              <circle cx="20" cy="20" r="9" />
            {:else}
              <circle cx="20" cy="20" r="9" class="ring" />
              <circle cx="20" cy="20" r="3" />
            {/if}
          </svg>
          <span class="label">{scene.label}</span>
        </button>
      {/each}
    </div>
  </Card>
{/if}

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
  /* ── Gård: three scenes, equal tiles, tap the lit one to turn off ───── */
  .scenes {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    align-items: stretch;
    height: 100%;
    min-height: 0;
  }

  .scene {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    min-height: 0;
    padding: 12px 8px;
    background: none;
    border: none;
    border-left: 1px solid var(--border);
    color: var(--dark);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: color 0.2s;
  }
  .scene:first-child {
    border-left: none;
  }
  .scene:active,
  .scene.active {
    color: var(--accent);
  }
  .scene:disabled {
    opacity: 0.3;
    cursor: default;
  }

  .mark {
    width: 40px;
    height: 40px;
    fill: currentColor;
    stroke: currentColor;
    stroke-width: 1.2;
    opacity: 0.55;
    transition: opacity 0.2s;
  }
  .scene.active .mark {
    opacity: 1;
  }
  .mark .ring {
    fill: none;
    stroke-dasharray: 3 4;
  }

  .label {
    font-size: 0.7rem;
    font-weight: 300;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  /* ── Seng / Toilet: same dial as the Hue rooms at home ─────────────── */
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

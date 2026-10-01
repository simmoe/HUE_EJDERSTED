<script lang="ts">
  import { onMount } from 'svelte';
  import Card from '$lib/Card.svelte';

  let scene = $state(1);
  let scenes = $state(3);
  let busy = false;

  onMount(() => {
    fetch('/api/gate/scene')
      .then((r) => r.json())
      .then((data) => {
        if (typeof data.scene === 'number') scene = data.scene;
        if (typeof data.scenes === 'number' && data.scenes > 0) scenes = data.scenes;
      })
      .catch(() => {});
  });

  async function next() {
    if (busy) return;
    const previous = scene;
    const upcoming = (scene % scenes) + 1;
    scene = upcoming;
    busy = true;
    try {
      const r = await fetch('/api/gate/scene', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene: upcoming }),
      });
      if (!r.ok) scene = previous;
    } catch {
      scene = previous;
    } finally {
      busy = false;
    }
  }
</script>

<Card name="Låge" status="">
  <div class="gate">
    <button type="button" class="scene" onclick={next} aria-label="Scene {scene}">
      {scene}
    </button>
  </div>
</Card>

<style>
  .gate {
    height: 100%;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .scene {
    height: 100%;
    max-height: 220px;
    aspect-ratio: 1;
    max-width: 100%;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.42);
    background: none;
    color: var(--light);
    font: inherit;
    font-size: 4.5rem;
    font-weight: 400;
    letter-spacing: -0.04em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .scene:active {
    background: rgba(255, 255, 255, 0.08);
  }
</style>

<script lang="ts">
  import type { Snippet } from 'svelte';

  let { name, status, online = false, pulse = false, children }: {
    name: string;
    status: string;
    online?: boolean;
    pulse?: boolean;
    children: Snippet;
  } = $props();
</script>

<article class="card" class:pulse>
  <div class="card-top">
    <span class="card-name">{name}</span>
    <span class="card-status" class:online>{status}</span>
  </div>
  {@render children()}
</article>

<style>
  .card {
    scroll-snap-align: start;
    scroll-snap-stop: always;
    min-height: calc(100dvh - 48px);
    display: grid;
    grid-template-rows: auto 1fr auto;
    align-items: center;
    padding: 24px 32px;
    border-radius: 0;
    background: none;
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: border-color 1.2s ease;
  }

  .card.pulse {
    border-color: rgba(0, 128, 200, 0.35);
    transition: border-color 0.15s ease;
  }
</style>

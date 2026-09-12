<script lang="ts">
  import type { Snippet } from 'svelte';

  let { name, status, online = false, pulse = false, onstatus, children }: {
    name: string;
    status: string;
    online?: boolean;
    pulse?: boolean;
    /** When set, the status text is a button (e.g. "offline" → reconnect). */
    onstatus?: () => void;
    children: Snippet;
  } = $props();
</script>

<article class="card" class:pulse class:no-header={!name && !status}>
  {#if name || status}
    <div class="card-top">
      <span class="card-name">{name}</span>
      {#if onstatus}
        <button type="button" class="card-status card-status-btn" class:online onclick={onstatus}>{status}</button>
      {:else}
        <span class="card-status" class:online>{status}</span>
      {/if}
    </div>
  {/if}
  {@render children()}
</article>

<style>
  .card {
    flex: 0 0 100%;
    height: 100%;
    min-height: 100%;
    max-height: 100%;
    overflow: hidden;
    display: grid;
    grid-template-rows: 38px minmax(0, 1fr);
    align-items: stretch;
    gap: 8px;
    padding: 18px 32px 62px;
    border-radius: 0;
    background: none;
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
    transition: border-color 1.2s ease;
  }

  .card.no-header {
    grid-template-rows: minmax(0, 1fr);
  }

  .card-status-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-family: inherit;
    padding: 6px 0 6px 16px;
    -webkit-tap-highlight-color: transparent;
  }
  .card-status-btn:active {
    color: var(--light);
  }

  .card.pulse {
    border-color: rgba(0, 128, 200, 0.35);
    transition: border-color 0.15s ease;
  }
</style>

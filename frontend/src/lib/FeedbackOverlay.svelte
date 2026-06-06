<script lang="ts">
  import { fade, scale } from 'svelte/transition';
  import { feedback } from '$lib/feedback.svelte';
</script>

{#if feedback.visible}
  <div class="feedback-overlay" aria-live="polite" aria-atomic="true">
    <div
      class="feedback-panel"
      class:error={feedback.kind === 'error'}
      class:success={feedback.kind === 'success'}
      in:scale={{ duration: 160, start: 0.96 }}
      out:fade={{ duration: 140 }}
    >
      <span>{feedback.message}</span>
    </div>
  </div>
{/if}

<style>
  .feedback-overlay {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    pointer-events: none;
  }

  .feedback-panel {
    max-width: min(78vw, 360px);
    padding: 18px 22px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 24px;
    background: rgba(24, 24, 24, 0.72);
    box-shadow: 0 22px 70px rgba(0, 0, 0, 0.45);
    color: #f0f0f0;
    font-size: 0.9rem;
    font-weight: 300;
    letter-spacing: 0.04em;
    line-height: 1.35;
    text-align: center;
    text-wrap: balance;
    -webkit-backdrop-filter: blur(28px) saturate(1.4);
    backdrop-filter: blur(28px) saturate(1.4);
  }

  .feedback-panel.error {
    border-color: rgba(255, 135, 135, 0.22);
    color: #ffd4d4;
  }

  .feedback-panel.success {
    border-color: rgba(190, 255, 205, 0.18);
    color: #e5ffe8;
  }
</style>

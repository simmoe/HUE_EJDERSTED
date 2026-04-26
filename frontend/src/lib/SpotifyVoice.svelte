<script lang="ts">
  /**
   * Stemme → hub returnerer kun kø-metadata; afspilning styres på +page.
   */
  import { onMount } from 'svelte';

  let {
    onvoice,
  }: {
    onvoice?: (data: Record<string, unknown>) => void;
  } = $props();

  let listening = $state(false);
  let feedback = $state('');
  let feedbackTimer: ReturnType<typeof setTimeout>;

  // Long-press skifter sprog: tap = engelsk, hold = dansk.
  const LONG_PRESS_MS = 450;
  let pressTimer: ReturnType<typeof setTimeout> | null = null;
  let longPressArmed = $state(false);
  let pressActive = false;

  function showFeedback(text: string, duration = 3000) {
    feedback = text;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { feedback = ''; }, duration);
  }

  async function handleResult(transcript: string) {
    listening = false;
    showFeedback(transcript, 8000);
    try {
      const r = await fetch('/api/spotify/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript }),
      });
      const data = (await r.json()) as Record<string, unknown>;
      onvoice?.(data);
      if (data.ok === false && data.error) {
        showFeedback(String(data.error));
      } else if (data.action === 'pause') {
        showFeedback(data.ok ? 'pause' : 'pause fejlede');
      } else if (data.action === 'local_nav') {
        showFeedback('');
      } else if (data.action === 'use_play_button') {
        showFeedback('Tryk play');
      } else if (data.action === 'enqueue' || data.action === 'enqueue_queue') {
        const lab = data.label ?? data.name;
        showFeedback(typeof lab === 'string' && lab ? lab : 'Tilføjet til kø');
      } else if (data.name && typeof data.name === 'string') {
        showFeedback(data.name);
      } else if (!data.ok && data.action === 'search') {
        showFeedback('ikke fundet');
      }
    } catch {
      showFeedback('fejl');
    }
  }

  function startListening(lang: 'en-US' | 'da-DK') {
    if (listening) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { showFeedback('voice ikke understøttet'); return; }
    const recognition = new SR();
    recognition.lang = lang;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    recognition.continuous = false;
    recognition.onstart = () => { listening = true; feedback = ''; };
    recognition.onerror = () => { listening = false; showFeedback('prøv igen'); };
    recognition.onnomatch = () => { listening = false; showFeedback('forstod ikke'); };
    recognition.onresult = (e: any) => handleResult(e.results[0][0].transcript);
    recognition.onend = () => { listening = false; };
    recognition.start();
  }

  function clearPressTimer() {
    if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
  }

  function onPressStart(e: PointerEvent) {
    if (listening) return;
    e.preventDefault();
    pressActive = true;
    longPressArmed = false;
    clearPressTimer();
    pressTimer = setTimeout(() => {
      if (pressActive) longPressArmed = true;
    }, LONG_PRESS_MS);
  }

  function onPressEnd(e: PointerEvent) {
    if (!pressActive) return;
    e.preventDefault();
    pressActive = false;
    clearPressTimer();
    const lang: 'en-US' | 'da-DK' = longPressArmed ? 'da-DK' : 'en-US';
    longPressArmed = false;
    startListening(lang);
  }

  function onPressCancel() {
    pressActive = false;
    longPressArmed = false;
    clearPressTimer();
  }

  onMount(() => () => { clearTimeout(feedbackTimer); clearPressTimer(); });
</script>

<div class="center-area">
  <button
    type="button"
    class="voice-btn"
    class:listening
    class:armed-da={longPressArmed}
    onpointerdown={onPressStart}
    onpointerup={onPressEnd}
    onpointerleave={onPressCancel}
    onpointercancel={onPressCancel}
    aria-label="Stemme — kort tryk = engelsk, langt tryk = dansk"
    title="Stemme — kort tryk EN, langt tryk DA"
  >
    <span class="voice-ring"></span>
    <svg class="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
    {#if longPressArmed}
      <span class="lang-badge">DA</span>
    {/if}
  </button>

  {#if feedback}
    <span class="feedback">{feedback}</span>
  {:else if listening}
    <span class="feedback listening-text">lytter</span>
  {/if}
</div>

<style>
  .center-area {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 28px;
  }

  .voice-btn {
    position: relative;
    width: 140px;
    height: 140px;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.06);
    background: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-tap-highlight-color: transparent;
    transition: border-color 0.4s ease;
    overflow: hidden;
    color: #595959;
  }

  .voice-btn:active {
    border-color: rgba(0, 128, 200, 0.4);
  }

  .voice-btn.listening {
    border-color: rgba(0, 128, 200, 0.35);
    color: #0080c8;
  }

  .voice-btn.armed-da {
    border-color: rgba(220, 200, 110, 0.55);
    color: #d8c87a;
  }

  .lang-badge {
    position: absolute;
    bottom: 22px;
    font-size: 0.7rem;
    font-weight: 400;
    letter-spacing: 0.12em;
    color: #d8c87a;
    pointer-events: none;
  }

  .mic-icon {
    width: 28px;
    height: 28px;
    transition: color 0.3s;
  }

  .voice-ring {
    position: absolute;
    inset: -1px;
    border-radius: 50%;
    border: 2px solid transparent;
    transition: border-color 0.3s ease;
  }

  .voice-btn.listening .voice-ring {
    border-color: #0080c8;
    animation: ring-pulse 1.5s ease-in-out infinite;
  }

  @keyframes ring-pulse {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50%      { opacity: 1;   transform: scale(1.04); }
  }

  .feedback {
    font-size: 0.75rem;
    font-weight: 300;
    letter-spacing: 0.06em;
    color: #ebebeb;
    text-align: center;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .listening-text {
    color: #0080c8;
  }
</style>

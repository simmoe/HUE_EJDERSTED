<script lang="ts">
  /**
   * SpotifyVoice — music controls: voice search, play/pause, radio station.
   */
  import { onMount } from 'svelte';

  let listening = $state(false);
  let feedback = $state('');
  let feedbackTimer: ReturnType<typeof setTimeout>;
  let isPlaying = $state(false);
  let npTitle = $state('');
  let npArtist = $state('');
  let pollInterval: ReturnType<typeof setInterval>;

  function showFeedback(text: string, duration = 3000) {
    feedback = text;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { feedback = ''; }, duration);
  }

  async function pollNowPlaying() {
    try {
      const r = await fetch('/api/spotify/now-playing');
      if (r.ok) {
        const data = await r.json();
        isPlaying = data.is_playing ?? false;
        npTitle = data.name || '';
        npArtist = data.artist || '';
      }
    } catch {}
  }

  onMount(() => {
    pollNowPlaying();
    pollInterval = setInterval(pollNowPlaying, 5000);
    return () => clearInterval(pollInterval);
  });

  async function handleResult(transcript: string) {
    listening = false;
    showFeedback(transcript, 8000);
    try {
      const r = await fetch('/api/spotify/voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript }),
      });
      const data = await r.json();
      if (data.ok === false && data.error) {
        showFeedback(data.error);
      } else if (data.action === 'pause') {
        isPlaying = false;
        showFeedback('pause');
      } else if (data.action === 'skip') {
        showFeedback('skip');
      } else if (data.action === 'previous') {
        showFeedback('forrige');
      } else if (data.action === 'resume') {
        isPlaying = true;
        showFeedback('play');
      } else if (data.name) {
        isPlaying = true;
        showFeedback(data.name);
      } else if (!data.ok) {
        showFeedback('ikke fundet');
      }
    } catch {
      showFeedback('fejl');
    }
  }

  function startListening() {
    if (listening) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { showFeedback('voice ikke understøttet'); return; }
    const recognition = new SR();
    recognition.lang = 'en-US';
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

  async function togglePlayPause() {
    const endpoint = isPlaying ? '/api/spotify/pause' : '/api/spotify/resume';
    try {
      const r = await fetch(endpoint, { method: 'POST' });
      const data = await r.json();
      if (data.ok) isPlaying = !isPlaying;
    } catch {}
  }

  async function startRadio() {
    showFeedback('radio…', 8000);
    try {
      const r = await fetch('/api/spotify/radio', { method: 'POST' });
      const data = await r.json();
      if (data.ok) {
        isPlaying = true;
        showFeedback(data.name || 'radio');
      } else {
        showFeedback(data.error || 'fejl');
      }
    } catch {
      showFeedback('fejl');
    }
  }
</script>

<div class="music-grid">
  <div class="center-area">
    <!-- Voice button (hero) -->
    <button class="voice-btn" class:listening onclick={startListening}>
      <span class="voice-ring"></span>
      <svg class="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0" />
        <line x1="12" y1="17" x2="12" y2="21" />
      </svg>
    </button>

    <!-- Action row -->
    <div class="action-row">
      <button class="action-btn" onclick={togglePlayPause}>
        {isPlaying ? 'pause' : 'play'}
      </button>
      <button class="action-btn" onclick={startRadio}>
        radio
      </button>
    </div>

    <!-- Feedback -->
    {#if feedback}
      <span class="feedback">{feedback}</span>
    {:else if listening}
      <span class="feedback listening-text">lytter</span>
    {/if}
  </div>

  <!-- Now playing (bottom) -->
  {#if npTitle}
    <div class="now-playing">
      <span class="np-title">{npTitle}</span>
      {#if npArtist}<span class="np-artist">{npArtist}</span>{/if}
    </div>
  {/if}
</div>

<style>
  .music-grid {
    display: grid;
    grid-template-rows: 1fr auto;
    align-items: center;
    width: 100%;
    height: 100%;
    min-height: calc(100dvh - 48px);
  }

  .center-area {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 28px;
  }

  /* ── Voice button ──────────────────────────────────────────────────────── */
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

  /* ── Now playing ────────────────────────────────────────────────────────── */
  .now-playing {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    padding: 0 8px 4px;
    align-self: end;
    justify-self: center;
  }

  .np-title {
    font-size: 0.8rem;
    color: #ebebeb;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 260px;
  }

  .np-artist {
    font-size: 0.65rem;
    letter-spacing: 0.06em;
    color: #595959;
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 260px;
  }

  /* ── Feedback ──────────────────────────────────────────────────────────── */
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
    letter-spacing: 0.22em;
    text-transform: uppercase;
    font-size: 0.7rem;
  }
</style>

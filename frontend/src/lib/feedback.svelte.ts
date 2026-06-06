export type FeedbackKind = 'info' | 'success' | 'error';

export type FeedbackOptions = {
  kind?: FeedbackKind;
  duration?: number;
};

export const feedback = $state({
  message: '',
  kind: 'info' as FeedbackKind,
  visible: false,
});

let feedbackTimer: ReturnType<typeof setTimeout> | null = null;

export function clearFeedback() {
  if (feedbackTimer) {
    clearTimeout(feedbackTimer);
    feedbackTimer = null;
  }
  feedback.visible = false;
  feedback.message = '';
}

export function showFeedback(message: string, options: FeedbackOptions = {}) {
  const text = message.trim();
  if (!text) return;

  if (feedbackTimer) clearTimeout(feedbackTimer);
  feedback.message = text;
  feedback.kind = options.kind ?? 'info';
  feedback.visible = true;

  const duration = options.duration ?? (feedback.kind === 'error' ? 4_500 : 2_800);
  feedbackTimer = setTimeout(() => {
    feedback.visible = false;
    feedbackTimer = null;
  }, duration);
}

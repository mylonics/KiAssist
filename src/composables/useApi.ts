/**
 * Centralized API client for pywebview bridge communication.
 *
 * Provides type-safe access to the Python backend, request timeouts,
 * retry with exponential backoff, and unified error handling.
 */
import type { PyWebViewAPI } from '../types/pywebview';

// -----------------------------------------------------------------------
// Constants
// -----------------------------------------------------------------------

/** Default timeout for API calls (ms). */
export const API_TIMEOUT_MS = 30_000;

/** Default maximum retries for retryable operations. */
export const API_MAX_RETRIES = 3;

/** Base delay for exponential backoff (ms). */
export const API_RETRY_BASE_DELAY_MS = 1_000;

/** Polling intervals (ms). */
export const POLL_INTERVAL_MS = 2_000;
export const PROGRESS_POLL_INTERVAL_MS = 400;

/** UI feedback durations (ms). */
export const TOAST_DURATION_MS = 2_000;

/** Maximum entries kept in activity panels. */
export const MAX_ACTIVITY_ENTRIES = 500;
export const MAX_LLM_LOG_ENTRIES = 200;

/** Truncation limits for display. */
export const MAX_DISPLAY_LENGTH = 120;

// -----------------------------------------------------------------------
// API Access
// -----------------------------------------------------------------------

/**
 * Get the typed pywebview API, or `null` if unavailable.
 *
 * Uses the global type declaration from `types/pywebview.ts` — no `any` cast.
 */
export function getApi(): PyWebViewAPI | null {
  return window.pywebview?.api ?? null;
}

/**
 * Wait for the pywebview API bridge to become available.
 * Resolves `true` when ready, `false` on timeout.
 */
export function waitForApi(timeoutMs = 5_000): Promise<boolean> {
  if (window.pywebview?.api) return Promise.resolve(true);
  return new Promise((resolve) => {
    const onReady = () => resolve(!!window.pywebview?.api);
    window.addEventListener('pywebviewready', onReady, { once: true });
    const timer = setTimeout(() => {
      window.removeEventListener('pywebviewready', onReady);
      resolve(!!window.pywebview?.api);
    }, timeoutMs);
    // Clean up timer if event fires first
    window.addEventListener('pywebviewready', () => clearTimeout(timer), { once: true });
  });
}

// -----------------------------------------------------------------------
// Error Handling
// -----------------------------------------------------------------------

/**
 * Normalize an unknown error into a user-friendly message.
 */
export function normalizeError(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === 'AbortError') return 'Request timed out';
    return error.message || 'Unknown error';
  }
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && 'message' in error) {
    return String((error as { message: unknown }).message);
  }
  return 'Unknown error';
}

/**
 * Check if an error indicates a stale pywebview callback (e.g. after HMR).
 */
export function isStaleCallbackError(error: unknown): boolean {
  const msg = String(error);
  return msg.includes('_returnValuesCallbacks') || msg.includes('pywebview');
}

// -----------------------------------------------------------------------
// Timeout Wrapper
// -----------------------------------------------------------------------

/**
 * Run an async function with a timeout. Rejects with an `AbortError`-style
 * message if the function doesn't resolve within `timeoutMs`.
 */
export async function withTimeout<T>(
  fn: () => Promise<T>,
  timeoutMs = API_TIMEOUT_MS,
): Promise<T> {
  if (timeoutMs <= 0) return fn();

  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(`Request timed out after ${timeoutMs}ms`);
      err.name = 'AbortError';
      reject(err);
    }, timeoutMs);
  });

  try {
    return await Promise.race([fn(), timeout]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

// -----------------------------------------------------------------------
// Retry with Exponential Backoff
// -----------------------------------------------------------------------

/**
 * Retry an async function with exponential backoff.
 *
 * @param fn          The function to retry.
 * @param maxRetries  Maximum number of attempts (default 3).
 * @param baseDelay   Base delay in ms (doubled on each retry).
 */
export async function withRetry<T>(
  fn: () => Promise<T>,
  maxRetries = API_MAX_RETRIES,
  baseDelay = API_RETRY_BASE_DELAY_MS,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (e) {
      lastError = e;
      // Don't retry on abort/timeout or stale callback
      if (
        (e instanceof Error && e.name === 'AbortError') ||
        isStaleCallbackError(e)
      ) {
        throw e;
      }
      if (attempt < maxRetries - 1) {
        const delay = baseDelay * Math.pow(2, attempt);
        await new Promise((r) => setTimeout(r, delay));
      }
    }
  }
  throw lastError;
}

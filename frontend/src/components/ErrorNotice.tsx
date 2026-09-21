import type { DescribedError } from "../api/client";

interface ErrorNoticeProps {
  error: DescribedError;
  onRetry?: () => void;
  retryLabel?: string;
  busy?: boolean;
}

/** A human-readable failure. The message comes from the backend's fixed, non-sensitive error
 * text (see describeApiError) -- never a raw provider error object. */
export default function ErrorNotice({ error, onRetry, retryLabel = "Try again", busy = false }: ErrorNoticeProps) {
  return (
    <div className="error-box" role="alert">
      <div>{error.message}</div>
      {error.retryable && onRetry && (
        <button className="btn" style={{ marginTop: 8 }} onClick={onRetry} disabled={busy}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}

import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { ApiError, ContractError, NetworkError } from "@/lib/api";
import { cn } from "@/lib/utils";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof NetworkError) {
    return "The API is not reachable. Start the Flask server on port 5000.";
  }
  if (error instanceof ContractError) {
    return "The API returned a shape this build does not understand.";
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function LoadingBlock({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={cn("loading-block", className)} aria-label="Loading" aria-busy="true">
      {Array.from({ length: lines }).map((_, index) => (
        <span key={index} style={{ width: `${88 - index * 13}%` }} />
      ))}
    </div>
  );
}

export function ErrorBlock({
  error,
  onRetry,
  title = "This data is unavailable",
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <div className={cn("state-block", className)} role="alert">
      <AlertTriangle size={16} className="text-[var(--color-caution)]" />
      <div>
        <strong>{title}</strong>
        <p>{errorMessage(error)}</p>
      </div>
      {onRetry && (
        <button type="button" className="ghost-button state-retry" onClick={onRetry}>
          <RefreshCw size={12} />
          Retry
        </button>
      )}
    </div>
  );
}

export function Notice({
  children,
  tone = "warn",
  className,
}: {
  children: ReactNode;
  tone?: "warn" | "info" | "bad";
  className?: string;
}) {
  return (
    <div className={cn("notice", `notice-${tone}`, className)} role={tone === "bad" ? "alert" : "status"}>
      <AlertTriangle size={13} />
      <span>{children}</span>
    </div>
  );
}

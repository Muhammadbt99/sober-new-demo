import { cn } from "@/lib/utils";

export function Wordmark({ className }: { className?: string }) {
  return (
    <div className={cn("wordmark", className)} aria-label="S.O.B.E.R.">
      <strong>S.O.B.E.R.</strong>
      <span>Safer routing / NYC</span>
    </div>
  );
}

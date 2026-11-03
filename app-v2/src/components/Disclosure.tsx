import { useId, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A collapsible section. The panels are dense, so secondary tools (compare,
 * severity, nearby stops) sit behind a header that says what they are and
 * summarises what is inside, and open on demand.
 */
export function Disclosure({
  title,
  icon,
  meta,
  defaultOpen = false,
  onOpenChange,
  children,
  className,
}: {
  title: string;
  icon?: ReactNode;
  /** A short summary shown on the closed header. */
  meta?: ReactNode;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();

  return (
    <section className={cn("disclosure", open && "is-open", className)}>
      <button
        type="button"
        className="disclosure-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => {
          setOpen((current) => {
            onOpenChange?.(!current);
            return !current;
          });
        }}
      >
        <span className="disclosure-title">
          {icon}
          {title}
        </span>
        {meta !== undefined && <span className="disclosure-meta">{meta}</span>}
        <ChevronDown size={14} className="disclosure-chevron" aria-hidden="true" />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            id={bodyId}
            className="disclosure-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
          >
            <div className="disclosure-inner">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

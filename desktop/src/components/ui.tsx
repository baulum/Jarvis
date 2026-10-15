// Small UI primitives. Deliberately few variants; hierarchy comes from type and spacing.
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Loader2, RotateCw, X } from "lucide-react";
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { riskLabel, type Tone } from "../lib/format";

type Variant = "primary" | "ghost" | "danger" | "quiet" | "outline";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-arc-deep text-arc border border-arc-dim/50 hover:bg-[#164453] hover:border-arc-dim disabled:opacity-40",
  outline: "border border-seam-hi text-ink hover:border-ink-faint hover:bg-plate-hi disabled:opacity-40",
  ghost: "text-ink-dim hover:text-ink hover:bg-plate-hi disabled:opacity-40",
  quiet: "text-ink-dim hover:text-arc disabled:opacity-40",
  danger: "border border-alarm/50 text-alarm hover:bg-alarm-deep disabled:opacity-40",
};

export function Button({
  variant = "outline",
  size = "md",
  busy = false,
  icon,
  children,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  busy?: boolean;
  icon?: ReactNode;
}) {
  const sz = size === "sm" ? "h-7 px-2.5 text-xs gap-1.5" : "h-8.5 px-3.5 text-[0.8125rem] gap-2";
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex shrink-0 items-center justify-center rounded-md font-medium whitespace-nowrap transition-colors duration-150 ${sz} ${VARIANTS[variant]} ${className}`}
    >
      {busy ? <Loader2 className="size-3.5 spin-slow" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  children,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={`inline-flex size-7 items-center justify-center rounded-md text-ink-dim transition-colors hover:bg-plate-hi hover:text-ink disabled:opacity-40 ${className}`}
    >
      {children}
    </button>
  );
}

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-arc shadow-[0_0_8px] shadow-arc/60",
  info: "bg-arc-dim",
  warn: "bg-ember shadow-[0_0_8px] shadow-ember/50",
  bad: "bg-alarm shadow-[0_0_8px] shadow-alarm/50",
  idle: "bg-ink-faint",
};

export function Dot({ tone, pulse = false, className = "" }: { tone: Tone; pulse?: boolean; className?: string }) {
  return (
    <span className={`relative inline-flex size-1.5 shrink-0 rounded-full ${TONE_DOT[tone]} ${className}`}>
      {pulse && <span className={`absolute inset-0 animate-ping rounded-full opacity-60 ${TONE_DOT[tone]}`} />}
    </span>
  );
}

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-arc",
  info: "text-ink-dim",
  warn: "text-ember",
  bad: "text-alarm",
  idle: "text-ink-faint",
};

export function StatusText({ tone, children, pulse }: { tone: Tone; children: ReactNode; pulse?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${TONE_TEXT[tone]}`}>
      <Dot tone={tone} pulse={pulse} />
      {children}
    </span>
  );
}

const RISK_STYLE: Record<string, string> = {
  read: "text-ink-faint border-seam-hi",
  low: "text-arc-dim border-arc-deep",
  medium: "text-ember border-ember/30",
  high: "text-ember border-ember/70 bg-ember-deep",
  critical: "text-alarm border-alarm/70 bg-alarm-deep",
};

/** Risk is a 5-step scale; the badge shows the step count so it reads without color too. */
export function RiskBadge({ risk }: { risk: string }) {
  const steps = { read: 1, low: 2, medium: 3, high: 4, critical: 5 }[risk] ?? 0;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-px text-2xs ${RISK_STYLE[risk] ?? RISK_STYLE.read}`}
      title={`Risikostufe: ${riskLabel(risk)}`}
    >
      <span className="flex gap-px" aria-hidden>
        {[1, 2, 3, 4, 5].map((i) => (
          <span key={i} className={`h-2 w-[3px] rounded-[1px] ${i <= steps ? "bg-current" : "bg-current opacity-20"}`} />
        ))}
      </span>
      {riskLabel(risk)}
    </span>
  );
}

export function Tag({ children, tone = "idle" }: { children: ReactNode; tone?: Tone }) {
  return (
    <span className={`inline-flex items-center rounded border border-seam-hi px-1.5 py-px text-2xs ${TONE_TEXT[tone]}`}>
      {children}
    </span>
  );
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2.5 py-2" aria-busy="true" aria-label="Lädt">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="h-3 animate-pulse rounded bg-plate-hi"
          style={{ width: `${88 - ((i * 23) % 40)}%`, animationDelay: `${i * 80}ms` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 px-1 py-8 text-sm">
      {icon && <div className="mb-1 text-ink-faint">{icon}</div>}
      <p className="type-title text-ink">{title}</p>
      {children && <div className="max-w-[60ch] text-ink-dim">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-alarm/30 bg-alarm-deep/40 px-3.5 py-3 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-alarm" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-ink">{message}</p>
        <p className="mt-0.5 text-xs text-ink-dim">Prüfe, ob der Core unter 127.0.0.1:8765 läuft.</p>
      </div>
      {onRetry && (
        <Button size="sm" variant="ghost" icon={<RotateCw className="size-3.5" />} onClick={onRetry}>
          Erneut laden
        </Button>
      )}
    </div>
  );
}

/** Standard view frame: content column with optional side column. */
export function Section({
  title,
  meta,
  actions,
  children,
  className = "",
}: {
  title?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`plate flex min-h-0 flex-col ${className}`}>
      {(title || actions) && (
        <header className="flex min-h-11 items-center gap-3 border-b border-seam px-4 py-2">
          <div className="flex min-w-0 flex-1 items-baseline gap-3">
            {title && <h2 className="type-title truncate text-[0.875rem] text-ink">{title}</h2>}
            {meta && <span className="truncate text-xs text-ink-faint">{meta}</span>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  items,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { id: T; label: string; count?: number }[];
}) {
  return (
    <div role="tablist" className="flex items-center gap-1">
      {items.map((it) => {
        const active = it.id === value;
        return (
          <button
            key={it.id}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(it.id)}
            className={`relative h-7 rounded-md px-2.5 text-xs transition-colors ${active ? "bg-plate-hi text-ink" : "text-ink-dim hover:text-ink"}`}
          >
            {it.label}
            {it.count !== undefined && <span className="type-data ml-1.5 text-ink-faint">{it.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-4.5 w-8 shrink-0 items-center rounded-full border transition-colors disabled:opacity-40 ${checked ? "border-arc-dim bg-arc-deep" : "border-seam-hi bg-void"}`}
    >
      <span
        className={`absolute size-3 rounded-full transition-transform duration-200 ease-out-quint ${checked ? "translate-x-[15px] bg-arc" : "translate-x-[2px] bg-ink-faint"}`}
      />
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs text-ink-dim">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-2xs text-ink-faint">{hint}</p>}
    </div>
  );
}

export function KeyValue({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-6 gap-y-2 text-[0.8125rem]">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-ink-dim">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CodeBlock({ value, className = "" }: { value: string; className?: string }) {
  return (
    <pre
      className={`type-data selectable max-h-72 overflow-auto rounded-md border border-seam bg-void p-3 whitespace-pre-wrap break-words text-ink-dim ${className}`}
    >
      {value}
    </pre>
  );
}

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  tone = "default",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  tone?: "default" | "danger";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("[data-autofocus], input, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-void/70 backdrop-blur-[2px]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onMouseDown={(e) => e.target === e.currentTarget && onClose()}
        >
          <motion.div
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, y: 8, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className={`w-[min(32rem,calc(100vw-2rem))] rounded-xl border bg-hull shadow-2xl shadow-black/60 ${tone === "danger" ? "border-alarm/40" : "border-seam-hi"}`}
          >
            <header className="flex items-center justify-between border-b border-seam px-5 py-3.5">
              <h2 className="type-title text-[0.95rem]">{title}</h2>
              <IconButton label="Schließen" onClick={onClose}>
                <X className="size-4" />
              </IconButton>
            </header>
            <div className="px-5 py-4 text-sm text-ink-dim">{children}</div>
            {footer && <footer className="flex justify-end gap-2 border-t border-seam px-5 py-3">{footer}</footer>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

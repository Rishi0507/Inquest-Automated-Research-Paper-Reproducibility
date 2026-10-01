import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";

export type Option = { value: string; label: string; hint?: string; group?: string };

type Props = {
  value: string | null;
  options: Option[];
  onChange: (value: string) => void;
  label?: string;          // accessible name
  placeholder?: string;
  size?: "default" | "small";
  block?: boolean;         // fill the container width
  menuWidth?: number;      // minimum popover width in px
};

/** A listbox dropdown: keyboard navigable, grouped, with an optional secondary line per option. */
export default function Select({ value, options, onChange, label, placeholder = "Select", size = "default", block, menuWidth = 280 }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const current = options.find((o) => o.value === value);

  const place = () => {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.max(r.width, menuWidth);
    // Open towards the side with room: buttons on the right half align the menu's right edge.
    const preferred = r.left > window.innerWidth / 2 ? r.right - width : r.left;
    const left = Math.min(preferred, window.innerWidth - width - 12);
    const up = r.bottom + 320 > window.innerHeight && r.top > 340;
    setRect({ left: Math.max(12, left), top: up ? r.top - 6 : r.bottom + 6, width, up });
  };

  useLayoutEffect(() => { if (open) place(); }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!list.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) setOpen(false);
    };
    const reflow = () => place();
    document.addEventListener("mousedown", close);
    window.addEventListener("resize", reflow);
    window.addEventListener("scroll", reflow, true);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("resize", reflow);
      window.removeEventListener("scroll", reflow, true);
    };
  }, [open]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const choose = (i: number) => {
    const o = options[i];
    if (o) onChange(o.value);
    setOpen(false);
    button.current?.focus();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open && ["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      setActive(Math.max(0, options.findIndex((o) => o.value === value)));
      setOpen(true);
      return;
    }
    if (!open) return;
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(options.length - 1, a + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(options.length - 1); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(active); }
    else if (e.key === "Tab") setOpen(false);
  };

  let lastGroup: string | undefined;
  return (
    <>
      <button ref={button} type="button" className={`dd ${size === "small" ? "dd-small" : ""} ${block ? "dd-block" : ""} ${open ? "dd-open" : ""}`}
        aria-haspopup="listbox" aria-expanded={open} aria-controls={id} aria-label={label}
        onClick={() => { setActive(Math.max(0, options.findIndex((o) => o.value === value))); setOpen((o) => !o); }} onKeyDown={onKey}>
        <span className="dd-value">{current?.label ?? <span className="muted">{placeholder}</span>}</span>
        <svg className="dd-chevron" width="10" height="6" viewBox="0 0 10 6" aria-hidden><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {createPortal(
        <AnimatePresence>
          {open && rect && (
            <motion.div ref={list} id={id} role="listbox" aria-label={label} className="dd-menu" tabIndex={-1} onKeyDown={onKey}
              style={{ left: rect.left, width: rect.width, ...(rect.up ? { bottom: window.innerHeight - rect.top } : { top: rect.top }) }}
              initial={{ opacity: 0, y: rect.up ? 4 : -4, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: rect.up ? 4 : -4, scale: 0.98 }} transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}>
              {options.map((o, i) => {
                const header = o.group && o.group !== lastGroup ? o.group : null;
                lastGroup = o.group;
                return (
                  <div key={o.value}>
                    {header && <div className="dd-group">{header}</div>}
                    <div role="option" aria-selected={o.value === value} data-index={i}
                      className={`dd-option ${i === active ? "dd-active" : ""}`}
                      onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(i)}>
                      <span className="dd-check" aria-hidden>{o.value === value ? (
                        <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 6.2l2.2 2.2 4.8-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
                      ) : null}</span>
                      <span className="dd-text">
                        <span className="dd-label">{o.label}</span>
                        {o.hint && <span className="dd-hint">{o.hint}</span>}
                      </span>
                    </div>
                  </div>
                );
              })}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}

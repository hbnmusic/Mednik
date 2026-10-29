"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as DropdownPrimitive from "@radix-ui/react-dropdown-menu";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as SliderPrimitive from "@radix-ui/react-slider";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { cva, type VariantProps } from "class-variance-authority";
import { Check, ChevronDown, X } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

/* ------------------------------ Button ------------------------------ */

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[5px] text-sm font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary: "bg-accent text-on-accent hover:opacity-90",
        quiet: "text-ink-2 hover:bg-raised hover:text-ink",
        outline: "border border-line bg-panel text-ink hover:bg-raised",
      },
      size: { sm: "h-7 px-2.5", md: "h-8 px-3", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "outline", size: "md" },
  },
);

export function Button({ className, variant, size, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

/* ------------------------------ Select ------------------------------ */

export function Select({ value, onValueChange, options, placeholder, label, className, disabled }: {
  value: string | undefined;
  onValueChange: (v: string) => void;
  options: { value: string; label: string; hint?: string }[];
  placeholder?: string;
  label?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <SelectPrimitive.Trigger aria-label={label}
        className={cn("inline-flex h-8 min-w-0 items-center justify-between gap-2 rounded-[5px] border border-line bg-panel px-2.5 text-sm text-ink hover:bg-raised", className)}>
        <span className="truncate"><SelectPrimitive.Value placeholder={placeholder} /></span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-3" />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content position="popper" sideOffset={4}
          className="z-50 max-h-80 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-line bg-panel shadow-lg">
          <SelectPrimitive.Viewport className="p-1">
            {options.map((o) => (
              <SelectPrimitive.Item key={o.value} value={o.value}
                className="relative flex cursor-default select-none items-center rounded-[4px] py-1.5 pl-7 pr-3 text-sm text-ink outline-none data-[highlighted]:bg-accent-wash">
                <SelectPrimitive.ItemIndicator className="absolute left-2"><Check className="h-3.5 w-3.5" /></SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
                {o.hint && <span className="ml-2 text-2xs text-ink-3">{o.hint}</span>}
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

/* ------------------------------ Tabs ------------------------------ */

export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...p }: TabsPrimitive.TabsListProps) {
  return <TabsPrimitive.List className={cn("flex h-10 items-end gap-5", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: TabsPrimitive.TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={cn("relative -mb-px h-10 border-b-2 border-transparent px-0.5 text-sm font-medium text-ink-3 hover:text-ink focus-visible:outline-none focus-visible:bg-accent-wash rounded-t-[3px] data-[state=active]:border-accent data-[state=active]:text-ink", className)}
      {...p}
    />
  );
}
export const TabsContent = TabsPrimitive.Content;

/* ------------------------------ Slider ------------------------------ */

export function Slider({ value, onValueChange, onValueCommit, min = 0.1, max = 1, step = 0.01, label }: {
  value: number; onValueChange: (v: number) => void; onValueCommit?: (v: number) => void; min?: number; max?: number; step?: number; label: string;
}) {
  return (
    <SliderPrimitive.Root className="relative flex h-4 w-full touch-none select-none items-center" value={[value]} min={min} max={max} step={step}
      onValueChange={(v) => onValueChange(v[0])} onValueCommit={(v) => onValueCommit?.(v[0])}>
      <SliderPrimitive.Track className="relative h-[3px] grow rounded-full bg-sunken">
        <SliderPrimitive.Range className="absolute h-full rounded-full bg-ink-3" />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb aria-label={label} className="block h-3.5 w-3.5 rounded-full border-2 border-accent bg-panel" />
    </SliderPrimitive.Root>
  );
}

/* ------------------------------ Toggle group ------------------------------ */

export function Segmented({ value, onValueChange, options, label }: {
  value: string; onValueChange: (v: string) => void; options: { value: string; label: string }[]; label: string;
}) {
  return (
    <ToggleGroupPrimitive.Root type="single" value={value} onValueChange={(v) => v && onValueChange(v)} aria-label={label}
      className="inline-flex h-8 items-center rounded-[5px] border border-line bg-panel p-0.5">
      {options.map((o) => (
        <ToggleGroupPrimitive.Item key={o.value} value={o.value}
          className="h-6 rounded-[3px] px-2.5 text-xs font-medium text-ink-3 hover:text-ink data-[state=on]:bg-raised data-[state=on]:text-ink">
          {o.label}
        </ToggleGroupPrimitive.Item>
      ))}
    </ToggleGroupPrimitive.Root>
  );
}

/* ------------------------------ Dialog / Sheet ------------------------------ */

export function Dialog({ open, onOpenChange, title, description, children, wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string; children: React.ReactNode; wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content className={cn("fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-32px)] -translate-x-1/2 rounded-lg border border-line bg-panel p-5 shadow-2xl", wide ? "max-w-2xl" : "max-w-lg")}>
          <DialogPrimitive.Title className="text-lg font-semibold">{title}</DialogPrimitive.Title>
          {description && <DialogPrimitive.Description className="mt-1 text-sm text-ink-2">{description}</DialogPrimitive.Description>}
          <div className="mt-4">{children}</div>
          <DialogPrimitive.Close className="absolute right-3 top-3 rounded p-1 text-ink-3 hover:bg-raised hover:text-ink" aria-label="Close"><X className="h-4 w-4" /></DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Sheet({ open, onOpenChange, title, subtitle, children }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: React.ReactNode; subtitle?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content onInteractOutside={(e) => e.preventDefault()}
          className="fixed bottom-7 right-0 top-0 z-30 flex w-[min(620px,100vw)] flex-col border-l border-line bg-panel shadow-[-12px_0_32px_-16px_rgba(0,0,0,0.35)]">
          <div className="flex items-start gap-3 border-b border-line px-5 py-3.5">
            <div className="min-w-0 flex-1">
              <DialogPrimitive.Title className="text-lg font-semibold leading-snug">{title}</DialogPrimitive.Title>
              {subtitle && <DialogPrimitive.Description asChild><div className="mt-0.5 text-sm text-ink-2">{subtitle}</div></DialogPrimitive.Description>}
            </div>
            <DialogPrimitive.Close className="rounded p-1 text-ink-3 hover:bg-raised hover:text-ink" aria-label="Close panel"><X className="h-4 w-4" /></DialogPrimitive.Close>
          </div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/* ------------------------------ Tooltip ------------------------------ */

export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <TooltipPrimitive.Provider delayDuration={150}>
      <TooltipPrimitive.Root>
        <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
        <TooltipPrimitive.Portal>
          <TooltipPrimitive.Content sideOffset={6} className="z-50 max-w-xs rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs text-ink shadow-lg">
            {content}
          </TooltipPrimitive.Content>
        </TooltipPrimitive.Portal>
      </TooltipPrimitive.Root>
    </TooltipPrimitive.Provider>
  );
}

/* ------------------------------ Dropdown ------------------------------ */

export function Menu({ trigger, items }: { trigger: React.ReactNode; items: { label: string; onSelect: () => void; hint?: string }[] }) {
  return (
    <DropdownPrimitive.Root>
      <DropdownPrimitive.Trigger asChild>{trigger}</DropdownPrimitive.Trigger>
      <DropdownPrimitive.Portal>
        <DropdownPrimitive.Content align="end" sideOffset={4} className="z-50 min-w-48 rounded-md border border-line bg-panel p-1 shadow-lg">
          {items.map((i) => (
            <DropdownPrimitive.Item key={i.label} onSelect={i.onSelect}
              className="flex cursor-default flex-col rounded-[4px] px-2.5 py-1.5 text-sm text-ink outline-none data-[highlighted]:bg-accent-wash">
              {i.label}
              {i.hint && <span className="text-2xs text-ink-3">{i.hint}</span>}
            </DropdownPrimitive.Item>
          ))}
        </DropdownPrimitive.Content>
      </DropdownPrimitive.Portal>
    </DropdownPrimitive.Root>
  );
}

/* ------------------------------ Evidence chips ------------------------------ */

const TIER_STYLE: Record<string, string> = {
  "1A": "bg-ink text-panel",
  "1B": "bg-ink-2 text-panel",
  "2": "border border-ink-3 text-ink-2",
  "3": "border border-dashed border-ink-3 text-ink-3",
};

export function TierChip({ tier, className }: { tier: string | null | undefined; className?: string }) {
  if (!tier) return <span className={cn("text-2xs text-ink-3", className)}>no tier</span>;
  return (
    <Tip content={`Evidence tier ${tier}`}>
      <span className={cn("inline-flex h-[18px] items-center rounded-[3px] px-1.5 font-cond text-2xs font-semibold tnum", TIER_STYLE[tier], className)}>T{tier}</span>
    </Tip>
  );
}

export function ValidityChip({ level, className }: { level: number | null | undefined; className?: string }) {
  if (!level) return null;
  return (
    <span className={cn("inline-flex h-[16px] min-w-[22px] items-center justify-center rounded-[3px] bg-panel/85 px-1 font-cond text-2xs font-semibold text-ink tnum ring-1 ring-ink/15", className)}>
      L{level}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-60 flex-col items-center justify-center px-6 text-center">
      <p className="text-lg font-medium text-ink">{title}</p>
      {children && <div className="mt-2 max-w-md text-sm text-ink-2">{children}</div>}
    </div>
  );
}

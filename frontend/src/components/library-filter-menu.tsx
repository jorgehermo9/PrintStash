import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";

/** A compact filter picker. Its fields remain multi-select and do not close on change. */
export function LibraryFilterMenu({
  label,
  count = 0,
  children,
}: {
  label: string;
  count?: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      role="dialog"
      aria-label={label}
      portal
      align="start"
      contentClassName="w-64 max-h-80 overflow-y-auto rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-lg"
      trigger={
        <Button
          data-menu-trigger
          type="button"
          variant={count > 0 ? "ghost" : "outline"}
          size="xs"
          className={count > 0 ? "h-8 bg-accent text-accent-foreground" : "h-8"}
          aria-label={label}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {label}
          {count > 0 && <span className="font-mono tabular-nums">{count}</span>}
          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
        </Button>
      }
    >
      <div role="group" aria-label={label}>
        {children}
      </div>
    </DropdownMenu>
  );
}

import { useEffect, useId, useState } from "react";
import type { KeyboardEvent } from "react";

export function useComboboxNav(
  itemCount: number,
  handlers: {
    onSelect: (index: number) => void;
    onCommitInput?: () => void;
    onClose?: () => void;
  },
) {
  const [activeIndex, setActiveIndex] = useState(-1);
  const listboxId = useId();

  useEffect(() => {
    if (activeIndex >= itemCount) setActiveIndex(itemCount - 1);
  }, [itemCount, activeIndex]);

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && itemCount > 0) {
      e.preventDefault();
      setActiveIndex((activeIndex + 1) % itemCount);
    } else if (e.key === "ArrowUp" && itemCount > 0) {
      e.preventDefault();
      setActiveIndex((activeIndex - 1 + itemCount) % itemCount);
    } else if (e.key === "Enter") {
      if (activeIndex >= 0 && activeIndex < itemCount) {
        e.preventDefault();
        handlers.onSelect(activeIndex);
        setActiveIndex(-1);
      } else if (handlers.onCommitInput) {
        e.preventDefault();
        handlers.onCommitInput();
      }
    } else if (e.key === "Escape") {
      handlers.onClose?.();
      setActiveIndex(-1);
    }
  }

  const optionId = (i: number) => `${listboxId}-opt-${i}`;

  return {
    activeIndex,
    setActiveIndex,
    listboxId,
    optionId,
    inputProps: {
      role: "combobox" as const,
      "aria-expanded": itemCount > 0,
      "aria-controls": listboxId,
      "aria-activedescendant": activeIndex >= 0 ? optionId(activeIndex) : undefined,
      "aria-autocomplete": "list" as const,
      onKeyDown,
    },
  };
}

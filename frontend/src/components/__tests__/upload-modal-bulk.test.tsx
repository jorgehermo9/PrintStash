import "@testing-library/jest-dom/vitest";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BulkFiles } from "@/components/upload-modal";
import type { BulkItem } from "@/lib/bulk-upload";

function makeFile(name: string): File {
  return new File(["data"], name);
}

function bulkItem(name: string, relPath = ""): BulkItem {
  return { file: makeFile(name), relPath };
}

// Minimal FileSystemEntry stand-in for a dropped file.
function fileEntry(fullPath: string): FileSystemEntry {
  const name = fullPath.split("/").pop() ?? "";
  return {
    isFile: true,
    isDirectory: false,
    fullPath,
    name,
    file: (resolve: (f: File) => void) => resolve(makeFile(name)),
  } as unknown as FileSystemEntry;
}

function renderBulk(over: Partial<Parameters<typeof BulkFiles>[0]> = {}) {
  const props = {
    items: [] as BulkItem[],
    fileInputRef: createRef<HTMLInputElement>(),
    folderInputRef: createRef<HTMLInputElement>(),
    onAddItems: vi.fn(),
    onRemove: vi.fn(),
    onClear: vi.fn(),
    ...over,
  };
  const utils = render(<BulkFiles {...props} />);
  return { ...utils, props };
}

describe("BulkFiles", () => {
  it("shows the drop-zone hint and a folder-select action when empty", () => {
    renderBulk();
    expect(screen.getByText(/drop 3d models or a folder here/i)).toBeInTheDocument();
    expect(screen.getByText(/subfolders become nested collections/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /select a folder/i })).toBeInTheDocument();
  });

  it("queues files picked through the file input", async () => {
    const { container, props } = renderBulk();
    const fileInput = container.querySelectorAll('input[type="file"]')[0];
    await userEvent.upload(fileInput as HTMLInputElement, [makeFile("foo.stl")]);

    expect(props.onAddItems).toHaveBeenCalledTimes(1);
    const passed = (props.onAddItems as ReturnType<typeof vi.fn>).mock.calls[0][0] as BulkItem[];
    expect(passed).toHaveLength(1);
    expect(passed[0].file.name).toBe("foo.stl");
    expect(passed[0].relPath).toBe("");
  });

  it("queues a folder picked through the folder input", async () => {
    const { container, props } = renderBulk();
    const folderInput = container.querySelectorAll('input[type="file"]')[1];
    expect(folderInput).toHaveAttribute("webkitdirectory");

    await userEvent.upload(folderInput as HTMLInputElement, [makeFile("a.stl")]);
    expect(props.onAddItems).toHaveBeenCalledTimes(1);
  });

  it("recurses a dropped entry tree into the queue", async () => {
    const { props } = renderBulk();
    const zone = screen.getByText(/drop 3d models or a folder here/i).closest("div") as HTMLElement;

    fireEvent.drop(zone, {
      dataTransfer: {
        items: [{ webkitGetAsEntry: () => fileEntry("/Lib/foo.stl") }],
        files: [],
      },
    });

    await waitFor(() => expect(props.onAddItems).toHaveBeenCalledTimes(1));
    const passed = (props.onAddItems as ReturnType<typeof vi.fn>).mock.calls[0][0] as BulkItem[];
    expect(passed[0].file.name).toBe("foo.stl");
    expect(passed[0].relPath).toBe("Lib");
  });

  it("falls back to a flat FileList when the entries API is unavailable", async () => {
    const { props } = renderBulk();
    const zone = screen.getByText(/drop 3d models or a folder here/i).closest("div") as HTMLElement;

    fireEvent.drop(zone, {
      dataTransfer: { items: [], files: [makeFile("flat.stl")] },
    });

    await waitFor(() => expect(props.onAddItems).toHaveBeenCalledTimes(1));
    const passed = (props.onAddItems as ReturnType<typeof vi.fn>).mock.calls[0][0] as BulkItem[];
    expect(passed[0].file.name).toBe("flat.stl");
    expect(passed[0].relPath).toBe("");
  });

  it("renders queued items with their folder prefix and a folder summary", () => {
    renderBulk({
      items: [
        bulkItem("top.stl", "Lib"),
        bulkItem("small.stl", "Lib/brackets"),
        bulkItem("loose.stl", ""),
      ],
    });
    expect(screen.getByText("top.stl")).toBeInTheDocument();
    expect(screen.getByText("Lib/brackets/")).toBeInTheDocument();
    // 3 files spanning 2 distinct folders.
    expect(screen.getByText(/3 files · 2 folders/i)).toBeInTheDocument();
  });

  it("invokes onRemove and onClear from the list controls", async () => {
    const { props } = renderBulk({ items: [bulkItem("a.stl", "Lib")] });
    await userEvent.click(screen.getByRole("button", { name: /remove a.stl/i }));
    expect(props.onRemove).toHaveBeenCalledWith(0);

    await userEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    expect(props.onClear).toHaveBeenCalledTimes(1);
  });
});

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GcodeViewer } from "@/components/gcode-viewer";
import { I18nProvider } from "@/lib/i18n";

const fetchMock = vi.fn();

vi.stubGlobal("fetch", fetchMock);

vi.mock("@react-three/fiber", () => ({
  Canvas: () => <div data-testid="canvas" />,
  useThree: () => ({ camera: { position: { set: vi.fn() } } }),
}));

vi.mock("@react-three/drei", () => ({
  OrbitControls: () => null,
  PerspectiveCamera: () => null,
}));

vi.mock("@/lib/preview-preferences", () => ({
  previewPixelRatio: () => 1,
  usePreviewPreferences: () => ({ previewQuality: "balanced" }),
}));

const TOOLPATH = "G90\nM82\nG1 Z0.2\nG1 X10 Y0 E0.1\nG1 X20 Y0 E0.2\n";

function textResponse(text: string, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock.mockReset();
  window.localStorage.clear();
});

describe("GcodeViewer", () => {
  it("exposes an accessible layer slider and pressed states for travel/bed toggles", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(textResponse(TOOLPATH));

    render(
      <I18nProvider>
        <GcodeViewer url="/api/v1/files/7/toolpath-preview" printerBedMm={{ x: 220, y: 220 }} />
      </I18nProvider>,
    );

    const slider = await screen.findByRole("slider", { name: "Current layer" });
    expect(slider).toHaveAttribute("max", "0");
    expect(screen.getByText(/Layer 1 \/ 1/)).toBeInTheDocument();

    const travel = screen.getByRole("button", { name: "Show travel moves" });
    const bed = screen.getByRole("button", { name: "Hide build plate" });
    expect(travel).toHaveAttribute("aria-pressed", "false");
    expect(bed).toHaveAttribute("aria-pressed", "true");

    await user.click(travel);
    await user.click(bed);

    expect(screen.getByRole("button", { name: "Hide travel moves" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Show build plate" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("localizes controls and presents a human error after a 401 without leaking auth data", async () => {
    localStorage.setItem("printstash.locale", "es");
    localStorage.setItem(
      "printstash.user",
      JSON.stringify({ id: 1, username: "tester", email: null, is_superuser: true }),
    );
    localStorage.setItem("printstash.token", "must-not-leak");
    fetchMock.mockResolvedValue(textResponse('{"detail":"not_authenticated"}', 401));
    const unauthorized = vi.fn();
    window.addEventListener("printstash:unauthorized", unauthorized);

    render(
      <I18nProvider>
        <GcodeViewer url="/api/v1/files/7/toolpath-preview" printerBedMm={{ x: 220, y: 220 }} />
      </I18nProvider>,
    );

    expect(
      await screen.findByText("No se pudo cargar la vista previa de la trayectoria."),
    ).toBeInTheDocument();
    expect(unauthorized).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/files/7/toolpath-preview",
      expect.objectContaining({ headers: {} }),
    );
    expect(JSON.stringify(fetchMock.mock.calls[0])).not.toContain("must-not-leak");
    window.removeEventListener("printstash:unauthorized", unauthorized);
  });
});

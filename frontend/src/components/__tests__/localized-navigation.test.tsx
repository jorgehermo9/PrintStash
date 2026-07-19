import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { LocaleToggle } from "@/components/locale-toggle";
import { I18nProvider } from "@/lib/i18n";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", email: null, is_superuser: true },
    logout: vi.fn(),
  }),
}));

vi.mock("@/lib/task-center", () => ({
  clearCompletedTasks: vi.fn(),
  listTasks: () => [],
  subscribeTasks: () => () => undefined,
}));

beforeEach(() => localStorage.setItem("printstash.locale", "en"));

it("updates navigation menu labels when locale changes", async () => {
  render(
    <MemoryRouter>
      <I18nProvider>
        <LocaleToggle />
        <BottomNavBar />
      </I18nProvider>
    </MemoryRouter>,
  );

  expect(screen.getByText("Vault")).toBeInTheDocument();
  expect(screen.getByText("Printers")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /Language/ }));

  expect(screen.getByText("Bóveda")).toBeInTheDocument();
  expect(screen.getByText("Impresoras")).toBeInTheDocument();
});

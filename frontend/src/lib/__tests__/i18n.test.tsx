import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { LocaleToggle } from "@/components/locale-toggle";
import { Localized } from "@/components/ui/localized";
import { I18nProvider, useI18n } from "@/lib/i18n";

function Probe() {
  const { t } = useI18n();
  return <p>{t("auth.welcome")}</p>;
}

it("persists locale and switches typed messages", async () => {
  localStorage.setItem("printstash.locale", "en");
  render(
    <I18nProvider>
      <LocaleToggle />
      <Probe />
    </I18nProvider>,
  );

  expect(screen.getByText("Welcome back")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /Language/ }));
  expect(screen.getByText("Te damos la bienvenida")).toBeInTheDocument();
  expect(localStorage.getItem("printstash.locale")).toBe("es");
  expect(document.documentElement.lang).toBe("es");
});

it("localizes page content and accessible labels", () => {
  localStorage.setItem("printstash.locale", "es");
  render(
    <I18nProvider>
      <Localized>
        <section aria-label="Settings sections">
          <h1>All Models</h1>
          <p>2 models total</p>
          <button title="New collection">Storage configuration</button>
        </section>
      </Localized>
    </I18nProvider>,
  );

  expect(screen.getByRole("heading", { name: "Todos los modelos" })).toBeInTheDocument();
  expect(screen.getByText("2 modelos en total")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Secciones de ajustes" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Configuración de almacenamiento" })).toHaveAttribute(
    "title",
    "Nueva colección",
  );
});

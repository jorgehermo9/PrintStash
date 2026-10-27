/*
 * The one control that switches the app's language.
 *
 * It is a single icon button, which makes its accessible name the entire label:
 * a screen-reader user has no glyph to interpret, so "Language: English" is the
 * only thing telling them what the button is and what state it is in. Losing that
 * leaves an unlabelled button in the header.
 *
 * Every registered language is a directly selectable, named menu item. The
 * anchored menu must work with the keyboard and return focus to its trigger
 * after selection or dismissal, so changing language never strands focus.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { LocaleToggle } from "@/components/locale-toggle";
import { localeDefinitions, SUPPORTED_LOCALES } from "@/lib/locale";
import { I18nProvider } from "@/lib/i18n";

function renderToggle() {
  return render(
    <I18nProvider>
      <LocaleToggle />
    </I18nProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("printstash.locale", "en");
});

describe("LocaleToggle", () => {
  it("names itself with the language currently in use", () => {
    renderToggle();

    expect(screen.getByRole("button", { name: /Language: English/ })).toBeInTheDocument();
  });

  it("switches to Spanish from English", async () => {
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Language/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "Español" }));

    expect(screen.getByRole("button", { name: /Idioma: Español/ })).toBeInTheDocument();
  });

  it("switches back to English from Spanish", async () => {
    localStorage.setItem("printstash.locale", "es");
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Idioma/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "English" }));

    expect(screen.getByRole("button", { name: /Language: English/ })).toBeInTheDocument();
  });

  it("names the language menu", async () => {
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Language/ }));

    expect(screen.getByRole("menu", { name: "Language" })).toBeVisible();
  });

  it.each([
    { label: "Enter", key: "{Enter}" },
    { label: "Space", key: " " },
  ])("opens from the trigger with $label", async ({ key }) => {
    const user = userEvent.setup();
    renderToggle();
    await user.tab();

    await user.keyboard(key);

    await waitFor(() =>
      expect(screen.getByRole("menuitemradio", { name: "English" })).toHaveFocus(),
    );
    expect(screen.getByRole("button", { name: /Language/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it.each([
    { label: "ArrowDown", keys: "{ArrowDown}", expected: "Español" },
    { label: "ArrowUp", keys: "{ArrowUp}", expected: "Español" },
    { label: "Home", keys: "{End}{Home}", expected: "English" },
    { label: "End", keys: "{End}", expected: "Español" },
  ])("moves to a language with $label", async ({ keys, expected }) => {
    const user = userEvent.setup();
    renderToggle();
    await user.click(screen.getByRole("button", { name: /Language/ }));
    await waitFor(() =>
      expect(screen.getByRole("menuitemradio", { name: "English" })).toHaveFocus(),
    );

    await user.keyboard(keys);

    expect(screen.getByRole("menuitemradio", { name: expected })).toHaveFocus();
  });

  it("returns focus to the translated trigger after keyboard selection", async () => {
    const user = userEvent.setup();
    renderToggle();
    await user.tab();
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.getByRole("menuitemradio", { name: "English" })).toHaveFocus(),
    );

    await user.keyboard("{ArrowDown}{Enter}");

    const trigger = screen.getByRole("button", { name: /Idioma: Español/ });
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("dismisses with Escape without changing the current language", async () => {
    const user = userEvent.setup();
    renderToggle();
    const trigger = screen.getByRole("button", { name: /Language: English/ });
    await user.click(trigger);
    await waitFor(() =>
      expect(screen.getByRole("menuitemradio", { name: "English" })).toHaveFocus(),
    );

    await user.keyboard("{ArrowDown}{Escape}");

    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(localStorage.getItem("printstash.locale")).toBe("en");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("persists a newly selected language for the next visit", async () => {
    const user = userEvent.setup();
    const { unmount } = renderToggle();
    await user.click(screen.getByRole("button", { name: /Language/ }));

    await user.click(screen.getByRole("menuitemradio", { name: "Español" }));
    unmount();
    renderToggle();

    expect(localStorage.getItem("printstash.locale")).toBe("es");
    expect(screen.getByRole("button", { name: /Idioma: Español/ })).toBeInTheDocument();
  });

  it("marks the stored language as the selected menu choice", async () => {
    localStorage.setItem("printstash.locale", "es");
    const user = userEvent.setup();
    renderToggle();

    await user.click(screen.getByRole("button", { name: /Idioma/ }));

    expect(screen.getByRole("menuitemradio", { name: "Español" })).toBeChecked();
    expect(screen.getByRole("menuitemradio", { name: "English" })).not.toBeChecked();
  });

  it("remembers the choice for the next visit", () => {
    localStorage.setItem("printstash.locale", "es");

    renderToggle();

    expect(screen.getByRole("button", { name: /Idioma/ })).toBeInTheDocument();
  });
});

describe("LocaleToggle registry", () => {
  it.each(SUPPORTED_LOCALES)("offers registered locale %s", async (locale) => {
    renderToggle();

    await userEvent.click(screen.getByRole("button", { name: /Language/ }));

    expect(
      screen.getByRole("menuitemradio", { name: localeDefinitions[locale].name }),
    ).toHaveAttribute("lang", locale);
  });
});

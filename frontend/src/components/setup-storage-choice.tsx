import { useEffect, useState } from "react";
import {
  defaultProviderValues,
  StorageProviderPicker,
  type ProviderValues,
} from "@/components/storage-provider-picker";
import { Button } from "@/components/ui/button";
import { getStorageProviders, getVaultConfig, prepareSetupStorage } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { setupErrorMessage, setupStorageBody } from "@/lib/setup-storage";
import { storageOperationMessage } from "@/lib/storage-operations";
import { providerFormError } from "@/lib/storage-provider-form";
import type { StorageProvider } from "@/types";

/**
 * The storage step for an owner provisioned from VAULT_SETUP_ADMIN_*.
 *
 * Browser registration chooses storage before the account exists; this owner
 * signed in first, so the same choice happens here. The server checks the
 * choice before persisting it, so a mistyped remote setting can be corrected.
 */
export function SetupStorageChoice({ onPrepared }: { onPrepared: () => void }) {
  const { t } = useI18n();
  const [providers, setProviders] = useState<StorageProvider[]>([]);
  const [providerId, setProviderId] = useState("local");
  const [values, setValues] = useState<ProviderValues>({});
  const [localRoots, setLocalRoots] = useState<ProviderValues>({});
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getStorageProviders(), getVaultConfig()]).then(
      ([catalog, config]) => {
        if (cancelled) return;
        const roots = { data_dir: config.data_dir, thumb_dir: config.thumb_dir };
        setProviders(catalog);
        setLocalRoots(roots);
        setValues(
          valuesFor(
            catalog.find((item) => item.id === "local"),
            roots,
          ),
        );
        setLoaded(true);
      },
      () => {
        if (!cancelled) setError(t("setup.failed"));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [t]);

  async function submit() {
    const provider = providers.find((item) => item.id === providerId);
    const invalid = !provider?.selectable
      ? provider?.disabled_reason
        ? storageOperationMessage(provider.disabled_reason, t)
        : t("setup.providerUnavailable")
      : providerFormError(provider, values);
    if (invalid) {
      setError(invalid);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await prepareSetupStorage(setupStorageBody(providerId, values));
      onPrepared();
    } catch (failure) {
      setError(setupErrorMessage(failure instanceof Error ? failure : new Error(), t));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      noValidate
      className="space-y-5"
      aria-label={t("setup.files")}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="text-sm leading-relaxed text-muted-foreground">
        {t("setup.chooseStorageHelp")}
      </p>
      {loaded && (
        <StorageProviderPicker
          onboarding
          disabled={busy}
          providers={providers}
          providerId={providerId}
          values={values}
          onProviderChange={(provider) => {
            setProviderId(provider.id);
            setValues(valuesFor(provider, localRoots));
            setError("");
          }}
          onValueChange={(name, value) => {
            setValues((current) => ({ ...current, [name]: value }));
            setError("");
          }}
        />
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <Button type="submit" className="h-11 w-full" loading={busy} disabled={!loaded}>
        {t("setup.chooseStorage")}
      </Button>
    </form>
  );
}

/** A provider's defaults; local storage starts at the deployment's own roots. */
function valuesFor(
  provider: StorageProvider | undefined,
  localRoots: ProviderValues,
): ProviderValues {
  if (!provider) return {};
  const values = { ...defaultProviderValues(provider) };
  if (provider.id === "local") Object.assign(values, localRoots);
  return values;
}

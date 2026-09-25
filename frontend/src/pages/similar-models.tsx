import { SimilarityQueue } from "@/components/similarity-queue";
import { SimilaritySettingsPanel } from "@/components/similarity-settings-panel";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n";

export default function SimilarModelsPage() {
  const { t } = useI18n();
  const { user } = useAuth();
  return (
    <PageContainer>
      <PageHeader title={t("similarity.title")} description={t("similarity.intro")} />
      {user?.is_superuser && (
        <div className="mb-5">
          <SimilaritySettingsPanel />
        </div>
      )}
      <SimilarityQueue />
    </PageContainer>
  );
}

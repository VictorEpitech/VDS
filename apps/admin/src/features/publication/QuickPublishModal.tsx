import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { useMedia, useScreens, type Screen } from "../../lib/queries";
import { mediaContent, refreshAfterOverride, type Override, type OverrideCreate } from "../overrides/overrides";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (message: string) => void;
  initialScreenId?: string;
}

export function QuickPublishModal({ isOpen, onClose, onSuccess, initialScreenId }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();

  const mediaQuery = useMedia();
  const screensQuery = useScreens();

  const [selectedMediaId, setSelectedMediaId] = useState<string>("");
  const [targetMode, setTargetMode] = useState<"all" | "custom">(initialScreenId ? "custom" : "all");
  const [selectedScreenIds, setSelectedScreenIds] = useState<string[]>(initialScreenId ? [initialScreenId] : []);
  const [searchMedia, setSearchMedia] = useState<string>("");
  const [durationHours, setDurationHours] = useState<number>(24);
  const [isFlash, setIsFlash] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<unknown | null>(null);

  const allScreens = useMemo(() => (screensQuery.data ?? []).filter((s) => !s.archived_at), [screensQuery.data]);
  const allMedia = useMemo(() => mediaQuery.data?.items ?? [], [mediaQuery.data]);

  const filteredMedia = useMemo(() => {
    if (!searchMedia.trim()) return allMedia;
    const q = searchMedia.toLowerCase();
    return allMedia.filter((m) => m.title.toLowerCase().includes(q));
  }, [allMedia, searchMedia]);

  const selectedMedia = useMemo(() => allMedia.find((m) => m.id === selectedMediaId), [allMedia, selectedMediaId]);

  const targetScreens = useMemo(() => {
    if (targetMode === "all") return allScreens;
    return allScreens.filter((s) => selectedScreenIds.includes(s.id));
  }, [targetMode, allScreens, selectedScreenIds]);

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (!selectedMedia) throw new Error("media_required");
      if (targetScreens.length === 0) throw new Error("target_required");

      const endsAt = new Date(Date.now() + durationHours * 3600 * 1000).toISOString();
      const content = mediaContent(selectedMedia);

      if (targetMode === "all") {
        const payload: OverrideCreate = {
          target_type: "organization",
          target_id: "default",
          kind: "playlist",
          content,
          ends_at: endsAt,
          priority: isFlash ? 100 : 0,
        };
        return [await api.post<Override>("/overrides", payload)];
      }

      const promises = targetScreens.map((s) => {
        const payload: OverrideCreate = {
          target_type: "screen",
          target_id: s.id,
          kind: "playlist",
          content,
          ends_at: endsAt,
          priority: isFlash ? 100 : 0,
        };
        return api.post<Override>("/overrides", payload);
      });

      return Promise.all(promises);
    },
    onSuccess: async () => {
      await refreshAfterOverride(qc);
      onSuccess(
        t("quick_publish.success_broadcast", {
          name: selectedMedia?.title,
          count: targetScreens.length,
        }),
      );
      onClose();
    },
    onError: (err: unknown) => {
      setErrorMsg(err);
    },
  });

  if (!isOpen) return null;

  const toggleScreen = (id: string) => {
    setSelectedScreenIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  return (
    <div className="quick-publish-backdrop" role="dialog" aria-modal="true" aria-labelledby={`${uid}-title`}>
      <div className="card quick-publish-modal">
        <header className="quick-publish-header">
          <div className="title-with-icon">
            <Icon name="schedule" />
            <h2 id={`${uid}-title`}>{t("quick_publish.title")}</h2>
          </div>
          <button type="button" className="btn btn-text close-btn" onClick={onClose} aria-label={t("quick_publish.close")}>
            <Icon name="close" />
          </button>
        </header>

        {errorMsg ? <ErrorAlert error={errorMsg} /> : null}

        <div className="quick-publish-body">
          {/* Étape 1 : Choix du média */}
          <section className="publish-step" aria-labelledby={`${uid}-step1`}>
            <div className="step-badge">1</div>
            <div className="step-content">
              <h3 id={`${uid}-step1`}>{t("quick_publish.step1_title")}</h3>
              <p className="meta">{t("quick_publish.step1_hint")}</p>

              <div className="media-selector-bar">
                <input
                  type="search"
                  className="input-text search-media-input"
                  placeholder={t("quick_publish.search_media")}
                  value={searchMedia}
                  onChange={(e) => setSearchMedia(e.target.value)}
                />
              </div>

              <div className="media-picker-grid" role="radiogroup" aria-label={t("quick_publish.select_media")}>
                {filteredMedia.length === 0 ? (
                  <p className="meta">{t("quick_publish.no_media")}</p>
                ) : (
                  filteredMedia.slice(0, 12).map((m) => {
                    const isPicked = m.id === selectedMediaId;
                    return (
                      <button
                        key={m.id}
                        type="button"
                        className={`media-picker-item ${isPicked ? "selected" : ""}`}
                        onClick={() => setSelectedMediaId(m.id)}
                        aria-pressed={isPicked}
                      >
                        <div className="media-picker-thumb">
                          <Icon name={m.kind === "video" ? "video" : "media"} />
                        </div>
                        <div className="media-picker-info">
                          <span className="media-title" title={m.title}>{m.title}</span>
                          <span className="meta">{m.kind}</span>
                        </div>
                      </button>
                    );
                  })
                )}
              </div>

              {selectedMedia ? (
                <div className="selected-media-preview">
                  <Icon name="check" />
                  <span>
                    <strong>{selectedMedia.title}</strong> ({selectedMedia.kind})
                  </span>
                </div>
              ) : null}
            </div>
          </section>

          {/* Étape 2 : Cibles de diffusion */}
          <section className="publish-step" aria-labelledby={`${uid}-step2`}>
            <div className="step-badge">2</div>
            <div className="step-content">
              <h3 id={`${uid}-step2`}>{t("quick_publish.step2_title")}</h3>
              <div className="seg" role="radiogroup">
                <label className="seg-item">
                  <input
                    type="radio"
                    name={`${uid}-target-mode`}
                    value="all"
                    checked={targetMode === "all"}
                    onChange={() => setTargetMode("all")}
                  />
                  <span>{t("quick_publish.target_all", { count: allScreens.length })}</span>
                </label>
                <label className="seg-item">
                  <input
                    type="radio"
                    name={`${uid}-target-mode`}
                    value="custom"
                    checked={targetMode === "custom"}
                    onChange={() => setTargetMode("custom")}
                  />
                  <span>{t("quick_publish.target_custom")}</span>
                </label>
              </div>

              {targetMode === "custom" ? (
                <div className="screen-chips-selector">
                  {allScreens.map((s) => {
                    const isChecked = selectedScreenIds.includes(s.id);
                    return (
                      <label key={s.id} className={`chip screen-chip ${isChecked ? "active" : ""}`}>
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleScreen(s.id)}
                        />
                        <Icon name="screen" />
                        <span>{s.name}</span>
                      </label>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </section>

          {/* Étape 3 : Durée et mode Flash */}
          <section className="publish-step" aria-labelledby={`${uid}-step3`}>
            <div className="step-badge">3</div>
            <div className="step-content">
              <h3 id={`${uid}-step3`}>{t("quick_publish.step3_title")}</h3>
              <div className="duration-options">
                {[
                  { hours: 4, label: "4 h" },
                  { hours: 24, label: "24 h" },
                  { hours: 72, label: "3 j" },
                  { hours: 168, label: "7 j" },
                ].map((opt) => (
                  <button
                    key={opt.hours}
                    type="button"
                    className={`btn btn-text chip ${durationHours === opt.hours ? "active" : ""}`}
                    onClick={() => setDurationHours(opt.hours)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>

              <div className="flash-broadcast-option">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={isFlash}
                    onChange={(e) => setIsFlash(e.target.checked)}
                  />
                  <span>
                    <strong>{t("quick_publish.flash_mode")}</strong> — {t("quick_publish.flash_hint")}
                  </span>
                </label>
              </div>
            </div>
          </section>
        </div>

        <footer className="quick-publish-footer actions">
          <button type="button" className="btn btn-text" onClick={onClose}>
            {t("quick_publish.cancel")}
          </button>
          <button
            type="button"
            className="btn btn-filled"
            disabled={!selectedMediaId || targetScreens.length === 0 || publishMutation.isPending}
            onClick={() => publishMutation.mutate()}
          >
            <Icon name="check" />
            {publishMutation.isPending ? t("quick_publish.publishing") : t("quick_publish.publish_cta")}
          </button>
        </footer>
      </div>
    </div>
  );
}

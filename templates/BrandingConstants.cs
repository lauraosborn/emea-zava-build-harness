using System;

namespace NAMESPACE.Branding
{
    /// <summary>
    /// Surface AI Factory — Section 1.1 Brand Consistency.
    ///
    /// Single source of truth for Zava branding and the Surface AI Factory
    /// watermark. Every surface that displays product identity (main window,
    /// About page, installer, splash, README generator) MUST read from here
    /// rather than hardcoding strings, so branding stays consistent.
    ///
    /// Do not fork or override these values per-view.
    /// </summary>
    public static class BrandingConstants
    {
        // ── Zava company branding (required on all applications) ─────────────

        /// <summary>Company name shown as the product owner. Always "Zava".</summary>
        public const string CompanyName = "Zava";

        /// <summary>Publisher string used by the installer / package manifest.</summary>
        public const string PublisherDisplayName = "Zava";

        /// <summary>
        /// Relative path to the Zava logo asset. The logo must accompany the
        /// company name wherever branding is displayed.
        /// </summary>
        public const string LogoAssetPath = "ms-appx:///Assets/zava-logo.png";

        // ── Surface AI Factory attribution (required watermark) ──────────────

        /// <summary>
        /// The exact watermark text mandated by the best-practices document.
        /// Do not reword, abbreviate, or localise this string.
        /// </summary>
        public const string Watermark = "Built by Surface AI Factory";

        /// <summary>Longer attribution used on the About page and README.</summary>
        public const string Attribution =
            "Built by Surface AI Factory — a Microsoft Surface engineering prototype for Zava.";

        /// <summary>
        /// The four surfaces that must carry attribution, per Section 1.1:
        /// main application UI, About page, README, and installer.
        /// </summary>
        public static readonly string[] RequiredAttributionSurfaces =
        {
            "Main application UI",
            "About page",
            "README",
            "Installer"
        };

        // ── AI transparency (Section 1.2) ────────────────────────────────────

        /// <summary>Label prefix for the currently loaded model.</summary>
        public const string ModelInUseLabel = "Model:";

        /// <summary>Label prefix for local AI status.</summary>
        public const string LocalAiStatusLabel = "Local AI:";

        /// <summary>Label prefix for NPU utilisation.</summary>
        public const string NpuUtilisationLabel = "NPU:";

        /// <summary>Label prefix for token generation progress.</summary>
        public const string TokenProgressLabel = "Tokens:";

        /// <summary>
        /// Convenience formatter for the status bar so every view renders the
        /// same transparency string.
        /// </summary>
        public static string FormatTransparencyLine(
            string modelName,
            bool localAiActive,
            double npuUtilisationPercent,
            double tokensPerSecond)
        {
            return string.Format(
                "{0} {1}  |  {2} {3}  |  {4} {5:F0}%  |  {6} {7:F1} tok/s",
                ModelInUseLabel, modelName ?? "unknown",
                LocalAiStatusLabel, localAiActive ? "On-device" : "Unavailable",
                NpuUtilisationLabel, npuUtilisationPercent,
                TokenProgressLabel, tokensPerSecond);
        }
    }
}

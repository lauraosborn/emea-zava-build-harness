using System;
using System.Reflection;
using Microsoft.UI.Xaml.Controls;
using NAMESPACE.Branding;

namespace NAMESPACE.Views
{
    /// <summary>
    /// Surface AI Factory — Section 1.1 Brand Consistency / 1.2 AI Transparency.
    ///
    /// The About page is one of the four surfaces that MUST carry Zava branding
    /// and the "Built by Surface AI Factory" watermark. All strings are read
    /// from <see cref="BrandingConstants"/> so branding cannot drift.
    /// </summary>
    public sealed partial class AboutPage : Page
    {
        public AboutPage()
        {
            InitializeComponent();
            ApplyBranding();
        }

        private void ApplyBranding()
        {
            // Zava company branding
            CompanyNameText.Text = BrandingConstants.CompanyName;

            // Mandatory Surface AI Factory watermark — exact text, never reworded
            WatermarkText.Text = BrandingConstants.Watermark;

            AppNameText.Text = GetAppDisplayName();
            VersionText.Text = $"Version {GetAppVersion()}";
        }

        /// <summary>
        /// Update the AI transparency block. Call this from your inference
        /// service so the About page reflects live local-AI state.
        /// </summary>
        public void UpdateTransparency(
            string modelName,
            bool localAiActive,
            double npuUtilisationPercent,
            double tokensPerSecond)
        {
            AboutModelNameText.Text =
                $"{BrandingConstants.ModelInUseLabel} {modelName ?? "unknown"}";
            AboutLocalAiText.Text =
                $"{BrandingConstants.LocalAiStatusLabel} {(localAiActive ? "On-device" : "Unavailable")}";
            AboutNpuStatusText.Text =
                $"{BrandingConstants.NpuUtilisationLabel} {npuUtilisationPercent:F0}%";
            AboutTokenSpeedText.Text =
                $"{BrandingConstants.TokenProgressLabel} {tokensPerSecond:F1} tok/s";
        }

        private static string GetAppDisplayName()
        {
            try
            {
                var name = Assembly.GetExecutingAssembly().GetName().Name;
                return string.IsNullOrWhiteSpace(name) ? "Application" : name;
            }
            catch
            {
                return "Application";
            }
        }

        private static string GetAppVersion()
        {
            try
            {
                var v = Assembly.GetExecutingAssembly().GetName().Version;
                return v?.ToString() ?? "1.0.0";
            }
            catch
            {
                return "1.0.0";
            }
        }
    }
}

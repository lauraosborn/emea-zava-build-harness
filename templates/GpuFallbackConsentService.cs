using System;
using System.Threading;
using System.Threading.Tasks;

namespace NAMESPACE.Services
{
    /// <summary>
    /// Surface AI Factory — GPU Fallback Consent.
    ///
    /// Best Practice §3 / §9: Local AI is NPU-first. GPU is only ever used as an
    /// explicit, user-approved fallback for models that are too large for the
    /// device's NPU (e.g. Whisper large-v3, Phi-3.5-Vision). The application must
    /// NEVER default to GPU silently — it must always attempt the NPU first and
    /// only prompt for GPU fallback when the NPU genuinely cannot host the model.
    ///
    /// Wire <see cref="ConsentPrompt"/> up to your UI (a WinUI3 ContentDialog is the
    /// expected implementation) so the user sees a real prompt, not a console message.
    /// </summary>
    public interface IGpuFallbackConsentService
    {
        /// <summary>
        /// Ask the user whether to fall back to GPU for the given model.
        /// Must be called every time a new model/session requires the decision —
        /// do not cache a prior "yes" across different models without re-asking,
        /// unless the user has explicitly opted into "remember my choice".
        /// </summary>
        Task<bool> RequestGpuFallbackConsentAsync(
            string modelName,
            string reason,
            CancellationToken cancellationToken = default);
    }

    public class GpuFallbackConsentService : IGpuFallbackConsentService
    {
        /// <summary>
        /// Delegate the host app assigns to show a real UI prompt (e.g. WinUI3 ContentDialog).
        /// Signature: (modelName, reason) => Task&lt;bool&gt; userApproved
        /// Defaults to a console-based prompt so the service is usable before UI wiring.
        /// </summary>
        public Func<string, string, CancellationToken, Task<bool>> ConsentPrompt { get; set; } =
            DefaultConsolePromptAsync;

        public async Task<bool> RequestGpuFallbackConsentAsync(
            string modelName,
            string reason,
            CancellationToken cancellationToken = default)
        {
            // Never assume consent. Always ask, always default to "false" (NPU/mock only)
            // if the prompt cannot be shown or the user does not respond.
            try
            {
                return await ConsentPrompt(modelName, reason, cancellationToken);
            }
            catch
            {
                return false;
            }
        }

        private static Task<bool> DefaultConsolePromptAsync(
            string modelName, string reason, CancellationToken cancellationToken)
        {
            Console.WriteLine(
                $"[GPU Fallback] '{modelName}' does not fit on the NPU ({reason}). " +
                "Allow this app to run it on the GPU instead? [y/N]");
            var key = Console.ReadKey(intercept: true);
            Console.WriteLine();
            return Task.FromResult(key.Key == ConsoleKey.Y);
        }
    }
}

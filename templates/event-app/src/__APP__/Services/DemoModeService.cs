using System.Text.RegularExpressions;

namespace __APP__.Services
{
    /// <summary>
    /// Demo Mode service: the last step of the inference hierarchy
    /// (NPU, then user-consented GPU, then Demo Mode, then error).
    /// Best Practice §9: Resilience &amp; Reliability.
    ///
    /// For live events, Demo Mode replays outputs that THIS app recorded from its
    /// own on-device model during the preflight warm-up. The UI always labels
    /// these as REPLAY so the audience is never misled about what is live.
    /// See <see cref="InferenceHardwareSelector"/> and <see cref="IGpuFallbackConsentService"/>.
    /// </summary>
    public class DemoModeService : IInferenceService
    {
        private readonly ReplayLibrary _replay;

        public DemoModeService() : this(ReplayLibrary.Load()) { }

        public DemoModeService(ReplayLibrary replay) => _replay = replay;

        public string ModelName => "Replay (recorded on-device output)";
        public bool IsReady => true;
        public bool IsNpuActive => false;
        public bool IsGpuActive => false;
        public HardwareTier ActiveTier => HardwareTier.MockDemo;

        public ReplayEntry? LastMatch { get; private set; }

        public Task<string> CompleteAsync(string prompt, InferenceOptions? options = null, CancellationToken cancellationToken = default)
        {
            LastMatch = _replay.Find(prompt);
            if (LastMatch is not null)
                return Task.FromResult(LastMatch.Response);

            return Task.FromResult(
                "## Replay unavailable for this prompt\n" +
                "The live on-device model is not ready, and no recorded output matches this prompt.\n" +
                "- Try one of the sample prompts (they were recorded on this device during preflight).\n" +
                "- Or wait for the model status on the right to show **Live**.\n" +
                "Nothing was sent to the cloud.");
        }

        public async IAsyncEnumerable<string> StreamAsync(
            string prompt,
            InferenceOptions? options = null,
            [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
        {
            var response = await CompleteAsync(prompt, options, cancellationToken);
            foreach (var word in Regex.Split(response, @"(?<=\s)"))
            {
                yield return word;
                await Task.Delay(16, cancellationToken);
            }
        }

        public Task LoadModelAsync(string modelId, CancellationToken cancellationToken = default) => Task.CompletedTask;
    }
}

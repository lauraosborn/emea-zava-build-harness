using System;
using System.Threading;
using System.Threading.Tasks;

namespace NAMESPACE.Services
{
    /// <summary>
    /// Surface AI Factory — Inference Hardware Selector.
    ///
    /// Enforces the required inference hierarchy (Best Practice §3 / §9):
    ///
    ///     1. NPU            — always attempted first, no exceptions.
    ///     2. GPU (fallback) — ONLY for models the NPU cannot host, and ONLY
    ///                         after the user explicitly approves via
    ///                         IGpuFallbackConsentService. Never a silent default.
    ///     3. Mock / Demo    — used if NPU fails and the user declines (or is not
    ///                         asked because the model is small and NPU should work).
    ///     4. Error          — surfaced only if Mock/Demo itself is unavailable.
    ///
    /// Silicon-agnostic: this selector does not care which vendor's NPU or GPU is
    /// present — it just orchestrates the decision tree above.
    /// </summary>
    public class InferenceHardwareSelector
    {
        private readonly HardwareCapabilityService _capability;
        private readonly IGpuFallbackConsentService _consent;
        private readonly NpuTelemetryService _telemetry;

        public InferenceHardwareSelector(
            HardwareCapabilityService capability,
            IGpuFallbackConsentService consent,
            NpuTelemetryService telemetry)
        {
            _capability = capability;
            _consent = consent;
            _telemetry = telemetry;
        }

        /// <summary>
        /// Selects the inference service to use for the given model, following the
        /// NPU-first / consent-gated-GPU / mock-fallback hierarchy.
        /// </summary>
        /// <param name="modelId">Model identifier (e.g. "whisper-large-v3", "phi-3.5-vision-instruct").</param>
        /// <param name="npuFactory">Factory that builds/loads the real NPU-backed inference service.</param>
        /// <param name="gpuFactory">Factory that builds/loads a GPU-backed inference service (only invoked after consent).</param>
        /// <param name="knownFootprintMb">Optional known model memory footprint override.</param>
        public async Task<IInferenceService> SelectAsync(
            string modelId,
            Func<string, CancellationToken, Task<IInferenceService>> npuFactory,
            Func<string, CancellationToken, Task<IInferenceService>> gpuFactory,
            int? knownFootprintMb = null,
            CancellationToken cancellationToken = default)
        {
            await _telemetry.DetectNpuAsync();

            // 1. Always attempt the NPU first — even for models flagged as "large".
            //    The capability estimate only informs whether we pre-warn the user;
            //    it never skips the NPU attempt.
            if (_telemetry.NpuDetected && _telemetry.NpuAccessible)
            {
                try
                {
                    using (_telemetry.MeasureModelLoad())
                    {
                        var npuService = await npuFactory(modelId, cancellationToken);
                        if (npuService.IsReady)
                        {
                            Console.WriteLine($"[HardwareSelector] '{modelId}' loaded on NPU.");
                            return npuService;
                        }
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[HardwareSelector] NPU load failed for '{modelId}': {ex.Message}");
                }
            }
            else
            {
                Console.WriteLine("[HardwareSelector] NPU not detected/accessible — will not use GPU by default; asking before any GPU fallback.");
            }

            // 2. NPU didn't work. Only now do we consider GPU — and only with consent.
            var fitsOnNpu = _capability.FitsOnNpu(modelId, knownFootprintMb);
            var reason = fitsOnNpu
                ? "the NPU failed to load the model at runtime"
                : _capability.DescribeShortfall(modelId, knownFootprintMb);

            var userApproved = await _consent.RequestGpuFallbackConsentAsync(modelId, reason, cancellationToken);

            if (userApproved)
            {
                try
                {
                    var gpuService = await gpuFactory(modelId, cancellationToken);
                    if (gpuService.IsReady)
                    {
                        Console.WriteLine($"[HardwareSelector] '{modelId}' loaded on GPU (user-approved fallback).");
                        return gpuService;
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[HardwareSelector] GPU fallback failed for '{modelId}': {ex.Message}");
                }
            }
            else
            {
                Console.WriteLine($"[HardwareSelector] GPU fallback declined or not offered for '{modelId}'.");
            }

            // 3. Neither NPU nor (approved) GPU worked — fall back to Demo/Mock mode.
            Console.WriteLine($"[HardwareSelector] Falling back to Demo Mode for '{modelId}'.");
            return new DemoModeService();

            // 4. If DemoModeService itself is ever unavailable, callers should catch
            //    exceptions from this method and surface a user-friendly error message
            //    per Best Practice §9 (Resilience & Reliability).
        }
    }
}

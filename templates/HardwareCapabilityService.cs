using System;
using System.Collections.Generic;

namespace NAMESPACE.Services
{
    /// <summary>
    /// Estimates whether a model can be hosted on the device's NPU, or whether it
    /// is large enough (e.g. Whisper large-v3, Phi-3.5-Vision) that a GPU fallback
    /// should be *offered* to the user. This service never makes the fallback
    /// decision itself — it only reports capability. The decision to actually use
    /// the GPU always goes through <see cref="IGpuFallbackConsentService"/>.
    /// </summary>
    public class HardwareCapabilityService
    {
        /// <summary>
        /// Conservative default NPU memory budget in MB. Override with the real
        /// value reported by the device (e.g. via DirectML device enumeration).
        /// </summary>
        public int NpuMemoryBudgetMb { get; set; } = 4096;

        /// <summary>
        /// Known large models that commonly exceed typical Copilot+ PC NPU budgets
        /// and may need a user-approved GPU fallback. Extend this catalog as needed —
        /// it is a starting point, not an exhaustive list.
        /// </summary>
        public static readonly IReadOnlyDictionary<string, int> KnownLargeModelFootprintsMb =
            new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
            {
                ["whisper-large-v3"] = 6000,
                ["whisper-large-v3-turbo"] = 4200,
                ["phi-3.5-vision-instruct"] = 5400,
                ["phi-3.5-vision-instruct-onnx"] = 5400,
            };

        /// <summary>
        /// Returns true if the model is expected to fit within the NPU memory budget.
        /// Unknown models are assumed to fit (optimistic default — NPU is attempted first
        /// regardless, and a runtime load failure is still handled gracefully).
        /// </summary>
        public bool FitsOnNpu(string modelId, int? knownFootprintMb = null)
        {
            var footprint = knownFootprintMb
                ?? (KnownLargeModelFootprintsMb.TryGetValue(modelId, out var mb) ? mb : (int?)null);

            if (footprint is null)
                return true; // unknown model — let the NPU load attempt decide

            return footprint.Value <= NpuMemoryBudgetMb;
        }

        /// <summary>
        /// Human-readable reason to show the user when requesting GPU fallback consent.
        /// </summary>
        public string DescribeShortfall(string modelId, int? knownFootprintMb = null)
        {
            var footprint = knownFootprintMb
                ?? (KnownLargeModelFootprintsMb.TryGetValue(modelId, out var mb) ? mb : (int?)null);

            if (footprint is null)
                return $"'{modelId}' size is unknown; NPU load will be attempted first.";

            return $"'{modelId}' needs ~{footprint} MB, which exceeds the {NpuMemoryBudgetMb} MB NPU budget on this device.";
        }
    }
}

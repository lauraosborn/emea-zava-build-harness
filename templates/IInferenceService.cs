using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace NAMESPACE.Services
{
    /// <summary>
    /// Which processor is actually serving inference for the current session.
    /// Best Practice §3/§9: NPU is always attempted first. GPU is an explicit,
    /// user-consented fallback for large models the NPU cannot host — never a
    /// silent default. MockDemo is the final fallback before surfacing an error.
    /// </summary>
    public enum HardwareTier
    {
        Npu,
        Gpu,
        MockDemo
    }

    /// <summary>
    /// Model-agnostic inference service interface.
    /// Implement this to swap models or runtimes without changing app logic.
    /// </summary>
    public interface IInferenceService
    {
        /// <summary>Current model display name.</summary>
        string ModelName { get; }

        /// <summary>Whether the model is loaded and ready.</summary>
        bool IsReady { get; }

        /// <summary>Whether inference is running on NPU.</summary>
        bool IsNpuActive { get; }

        /// <summary>
        /// Whether inference is currently running on GPU. This must only ever be true
        /// after the user has explicitly consented via IGpuFallbackConsentService —
        /// never set as a silent default.
        /// </summary>
        bool IsGpuActive { get; }

        /// <summary>Which hardware tier is currently serving inference.</summary>
        HardwareTier ActiveTier { get; }

        /// <summary>Generate a completion from a prompt.</summary>
        Task<string> CompleteAsync(
            string prompt,
            InferenceOptions? options = null,
            CancellationToken cancellationToken = default);

        /// <summary>Generate a streaming completion, yielding tokens as they arrive.</summary>
        IAsyncEnumerable<string> StreamAsync(
            string prompt,
            InferenceOptions? options = null,
            CancellationToken cancellationToken = default);

        /// <summary>Load (or reload) the model. Call at startup.</summary>
        Task LoadModelAsync(string modelId, CancellationToken cancellationToken = default);
    }

    public record InferenceOptions
    {
        public int MaxTokens { get; init; } = 512;
        public float Temperature { get; init; } = 0.7f;
        public string SystemPrompt { get; init; } = string.Empty;
    }
}

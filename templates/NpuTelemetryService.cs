using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;

namespace NAMESPACE.Services
{
    /// <summary>
    /// Captures and stores NPU inference telemetry locally.
    /// Best Practice §4: NPU Validation Requirements.
    /// </summary>
    public class NpuTelemetryService
    {
        private readonly string _logPath;
        private readonly List<TelemetryRecord> _sessionRecords = new();

        public double LastLatencyMs { get; private set; }
        public double LastTokensPerSecond { get; private set; }
        public double LastModelLoadMs { get; private set; }
        public bool NpuDetected { get; private set; }
        public bool NpuAccessible { get; private set; }
        public bool ModelOnNpu { get; private set; }

        public NpuTelemetryService(string logDirectory = "telemetry")
        {
            Directory.CreateDirectory(logDirectory);
            _logPath = Path.Combine(logDirectory, $"telemetry_{DateTime.UtcNow:yyyyMMdd}.jsonl");
        }

        public async Task DetectNpuAsync()
        {
            // Check for NPU via DirectML device enumeration or Foundry Local
            try
            {
                // Placeholder: replace with actual NPU detection via Windows.AI or Foundry Local
                NpuDetected = Environment.Is64BitOperatingSystem; // TODO: real detection
                NpuAccessible = NpuDetected;
            }
            catch
            {
                NpuDetected = false;
                NpuAccessible = false;
            }
            await Task.CompletedTask;
        }

        public IDisposable MeasureModelLoad() => new Stopwatch().AsDisposable(ms => LastModelLoadMs = ms);

        public IDisposable MeasureInference(int tokenCount = 0)
            => new Stopwatch().AsDisposable(ms =>
            {
                LastLatencyMs = ms;
                if (tokenCount > 0 && ms > 0)
                    LastTokensPerSecond = tokenCount / (ms / 1000.0);
            });

        public void RecordInference(string modelId, double latencyMs, double tokensPerSec, bool onNpu)
        {
            ModelOnNpu = onNpu;
            var record = new TelemetryRecord
            {
                Timestamp = DateTime.UtcNow,
                ModelId = modelId,
                LatencyMs = latencyMs,
                TokensPerSecond = tokensPerSec,
                OnNpu = onNpu
            };
            _sessionRecords.Add(record);

            // Append to local log (never sent externally — Best Practice §7)
            File.AppendAllText(_logPath, JsonSerializer.Serialize(record) + Environment.NewLine);
        }

        public IReadOnlyList<TelemetryRecord> GetSessionRecords() => _sessionRecords.AsReadOnly();
    }

    public record TelemetryRecord
    {
        public DateTime Timestamp { get; init; }
        public string ModelId { get; init; } = string.Empty;
        public double LatencyMs { get; init; }
        public double TokensPerSecond { get; init; }
        public bool OnNpu { get; init; }
        public double ModelLoadMs { get; init; }
    }

    internal static class StopwatchExtensions
    {
        public static IDisposable AsDisposable(this Stopwatch sw, Action<double> onStop)
        {
            sw.Restart();
            return new DelegateDisposable(() => { sw.Stop(); onStop(sw.Elapsed.TotalMilliseconds); });
        }
    }

    internal sealed class DelegateDisposable(Action action) : IDisposable
    {
        public void Dispose() => action();
    }
}

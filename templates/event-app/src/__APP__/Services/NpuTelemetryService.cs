using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;

namespace __APP__.Services
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

        public string NpuName { get; private set; } = "Not detected";

        public string CpuName { get; private set; } = "Unknown CPU";

        /// <summary>
        /// Detects the NPU through Windows Plug and Play: NPUs (Intel AI Boost, Qualcomm
        /// Hexagon, AMD Ryzen AI) register under the ComputeAccelerator device class.
        /// </summary>
        public async Task DetectNpuAsync()
        {
            await Task.Run(() =>
            {
                try
                {
                    using var searcher = new System.Management.ManagementObjectSearcher(
                        "SELECT Name, Status FROM Win32_PnPEntity WHERE PNPClass = 'ComputeAccelerator'");
                    foreach (var device in searcher.Get())
                    {
                        NpuName = device["Name"]?.ToString() ?? "NPU";
                        NpuDetected = true;
                        NpuAccessible = string.Equals(device["Status"]?.ToString(), "OK", StringComparison.OrdinalIgnoreCase);
                        break;
                    }

                    using var cpu = new System.Management.ManagementObjectSearcher("SELECT Name FROM Win32_Processor");
                    foreach (var p in cpu.Get())
                    {
                        CpuName = p["Name"]?.ToString()?.Trim() ?? CpuName;
                        break;
                    }
                }
                catch
                {
                    NpuDetected = false;
                    NpuAccessible = false;
                }
            });
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

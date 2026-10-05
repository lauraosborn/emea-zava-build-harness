using System.Diagnostics;

namespace __APP__.Services;

/// <summary>
/// Reads live NPU utilisation from the Windows "GPU Engine" performance counters
/// (the same source Task Manager uses). NPUs appear as an adapter that exposes only
/// compute engines, with no 3D engine, which is how the NPU adapter is identified.
/// </summary>
public sealed class NpuUtilisationMonitor
{
    private const string Category = "GPU Engine";
    private const string Counter = "Utilization Percentage";
    private Dictionary<string, CounterSample> _previous = new();
    private string? _npuLuid;
    private DateTime _lastLuidScan = DateTime.MinValue;

    public double CurrentPercent { get; private set; }
    public double PeakPercent { get; private set; }
    public bool Available { get; private set; } = true;

    public void ResetPeak() => PeakPercent = 0;

    public double Sample()
    {
        if (!Available)
            return 0;

        try
        {
            var category = new PerformanceCounterCategory(Category);
            var data = category.ReadCategory();
            var counter = data[Counter];
            if (counter is null)
            {
                Available = false;
                return 0;
            }

            if (_npuLuid is null || DateTime.UtcNow - _lastLuidScan > TimeSpan.FromSeconds(30))
            {
                _npuLuid = FindNpuLuid(counter.Values.Cast<InstanceData>().Select(i => i.InstanceName));
                _lastLuidScan = DateTime.UtcNow;
            }

            if (_npuLuid is null)
                return CurrentPercent = 0;

            var current = new Dictionary<string, CounterSample>();
            double total = 0;
            foreach (InstanceData instance in counter.Values)
            {
                if (!instance.InstanceName.Contains(_npuLuid, StringComparison.OrdinalIgnoreCase))
                    continue;

                current[instance.InstanceName] = instance.Sample;
                if (_previous.TryGetValue(instance.InstanceName, out var prev))
                    total += CounterSample.Calculate(prev, instance.Sample);
            }

            _previous = current;
            CurrentPercent = Math.Clamp(total, 0, 100);
            PeakPercent = Math.Max(PeakPercent, CurrentPercent);
            return CurrentPercent;
        }
        catch
        {
            Available = false;
            return 0;
        }
    }

    private static string? FindNpuLuid(IEnumerable<string> instances)
    {
        var engines = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
        foreach (var name in instances)
        {
            var luidStart = name.IndexOf("luid_", StringComparison.OrdinalIgnoreCase);
            var engStart = name.IndexOf("engtype_", StringComparison.OrdinalIgnoreCase);
            if (luidStart < 0 || engStart < 0)
                continue;

            var luidEnd = name.IndexOf("_phys", luidStart, StringComparison.OrdinalIgnoreCase);
            if (luidEnd < 0)
                continue;

            var luid = name[luidStart..luidEnd];
            var engine = name[(engStart + 8)..].Trim();
            if (!engines.TryGetValue(luid, out var set))
                engines[luid] = set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (engine.Length > 0)
                set.Add(engine);
        }

        return engines
            .Where(kv => kv.Value.Count > 0 && kv.Value.All(e => e.Equals("compute", StringComparison.OrdinalIgnoreCase)))
            .Select(kv => kv.Key)
            .FirstOrDefault();
    }
}

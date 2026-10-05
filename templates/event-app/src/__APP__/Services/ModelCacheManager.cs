using System.Runtime.InteropServices;
using System.Text.Json.Nodes;

namespace __APP__.Services;

/// <summary>
/// Manages the app's private Foundry Local model cache.
///
/// 1. Models already downloaded by the Foundry Local CLI are mirrored into the app cache
///    with NTFS hard links, so nothing is downloaded or copied twice and the user's
///    Foundry cache is never modified.
/// 2. OpenVINO's compiled-model cache (CACHE_DIR) is enabled for NPU models. The first
///    NPU load compiles the model (a few minutes); every later launch reuses the blob
///    and loads in seconds.
/// </summary>
public static class ModelCacheManager
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateHardLink(string lpFileName, string lpExistingFileName, IntPtr lpSecurityAttributes);

    private static readonly HashSet<string> CopyInsteadOfLink = new(StringComparer.OrdinalIgnoreCase)
    {
        "genai_config.json",
        "inference_model.json"
    };

    /// <summary>Mirror a model (by full id, e.g. "qwen2.5-1.5b-instruct-openvino-npu:5") from the Foundry CLI cache.</summary>
    public static bool TryMirrorFromFoundryCli(string modelId)
    {
        try
        {
            var source = AppPaths.FoundryCliCache;
            if (!Directory.Exists(source))
                return false;

            var folderName = modelId.Replace(':', '-');
            foreach (var publisherDir in Directory.EnumerateDirectories(source))
            {
                var modelDir = Path.Combine(publisherDir, folderName);
                if (!Directory.Exists(modelDir))
                    continue;

                var destDir = Path.Combine(AppPaths.Models, Path.GetFileName(publisherDir), folderName);
                foreach (var file in Directory.EnumerateFiles(modelDir, "*", SearchOption.AllDirectories))
                {
                    var target = Path.Combine(destDir, Path.GetRelativePath(modelDir, file));
                    if (File.Exists(target))
                        continue;

                    Directory.CreateDirectory(Path.GetDirectoryName(target)!);
                    if (CopyInsteadOfLink.Contains(Path.GetFileName(file)) || !CreateHardLink(target, file, IntPtr.Zero))
                        File.Copy(file, target);
                }

                foreach (var meta in Directory.EnumerateFiles(source, "*.json"))
                {
                    var target = Path.Combine(AppPaths.Models, Path.GetFileName(meta));
                    if (!File.Exists(target))
                        File.Copy(meta, target);
                }

                AppLog.Write($"Mirrored {modelId} from Foundry Local CLI cache.");
                return true;
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Mirror failed for {modelId}: {ex.Message}");
        }
        return false;
    }

    /// <summary>
    /// Adds OpenVINO cache_dir to every OpenVINO provider entry in genai_config.json under
    /// <paramref name="modelPath"/>. The file is rewritten (never edited in place) so a hard
    /// link back to another cache can never be modified.
    /// </summary>
    public static void EnableOpenVinoCompiledCache(string? modelPath)
    {
        if (string.IsNullOrEmpty(modelPath))
            return;

        try
        {
            var root = File.Exists(modelPath) ? Path.GetDirectoryName(modelPath)! : modelPath;
            if (!Directory.Exists(root))
                return;

            foreach (var config in Directory.EnumerateFiles(root, "genai_config.json", SearchOption.AllDirectories))
            {
                var node = JsonNode.Parse(File.ReadAllText(config));
                var changed = File.ReadAllText(config).Contains("\\u00", StringComparison.Ordinal);
                var providers = node?["model"]?["decoder"]?["session_options"]?["provider_options"] as JsonArray;
                if (providers is null)
                    continue;

                foreach (var provider in providers)
                {
                    if (provider?["OpenVINO"] is JsonObject ov &&
                        ov["cache_dir"]?.GetValue<string>() != AppPaths.OpenVinoCache)
                    {
                        ov["cache_dir"] = AppPaths.OpenVinoCache;
                        changed = true;
                    }
                }

                if (!changed)
                    continue;

                // genai_config.json is parsed by onnxruntime-genai, which rejects \uXXXX escapes,
                // so keep quotes inside nested JSON strings as plain \" escapes.
                var text = node!.ToJsonString(new System.Text.Json.JsonSerializerOptions
                {
                    WriteIndented = true,
                    Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
                });
                File.Delete(config);
                File.WriteAllText(config, text);
                AppLog.Write($"Enabled OpenVINO compiled cache for {config}");
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"Could not enable OpenVINO cache: {ex.Message}");
        }
    }
}

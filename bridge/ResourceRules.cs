using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace ModLingo.Bridge;

// No game dependencies: compiled unchanged by the cross-language contract tests.
internal static class ResourceRules
{
    private static readonly byte[] Raw = Read();
    private static readonly JsonElement Root = JsonDocument.Parse(Raw).RootElement;
    internal static int Version => Root.GetProperty("version").GetInt32();
    internal static string Hash => Convert.ToHexString(SHA256.HashData(Raw)).ToLowerInvariant();
    internal static int MaxDepth => Root.GetProperty("max_depth").GetInt32();
    internal static string LocalizedPattern => Root.GetProperty("localized_pattern").GetString()!;
    private static byte[] Read()
    {
        using var stream = typeof(ResourceRules).Assembly.GetManifestResourceStream("ModLingo.ResourceRules.json")
            ?? throw new InvalidOperationException("Missing embedded extraction contract.");
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }
    internal static bool ValidId(string? value) => value is not null && Full(value, Root.GetProperty("id_pattern").GetString()!);
    private static bool Full(string value, string pattern, RegexOptions options = RegexOptions.None) => Regex.IsMatch(value, @"\A(?:" + pattern + @")\z", options | RegexOptions.CultureInvariant);
    internal static JsonElement? AssetRule(string? asset)
    {
        if (asset is null || asset.Contains("{{")) return null;
        asset = asset.Replace('\\', '/');
        foreach (var rule in Root.GetProperty("assets").EnumerateArray())
        {
            string name = rule.GetProperty("name").GetString()!;
            if (name.EndsWith('/') ? asset.StartsWith(name, StringComparison.OrdinalIgnoreCase) : asset.Equals(name, StringComparison.OrdinalIgnoreCase)) return rule;
        }
        return null;
    }
    internal static string? CanonicalAsset(string? asset)
    {
        var rule = AssetRule(asset);
        if (rule is null) return null;
        string name = rule.Value.GetProperty("name").GetString()!;
        return name + asset!.Replace('\\', '/').Substring(name.Length);
    }
    private static string[]? Match(IReadOnlyList<string> path, JsonElement pattern, bool strict = true)
    {
        if (path.Count != pattern.GetArrayLength()) return null;
        var result = new string[path.Count];
        int index = 0;
        foreach (var part in pattern.EnumerateArray())
        {
            string field = part.GetString()!;
            string value = path[index];
            if (value is null || value.Contains('\u001f')) return null;
            if (field == "*")
            {
                if (strict && !ValidId(value)) return null;
                result[index] = value;
            }
            else if (field.Equals(value, StringComparison.OrdinalIgnoreCase)) result[index] = field;
            else return null;
            index++;
        }
        return result;
    }
    internal static string[]? TextPath(string asset, IReadOnlyList<string> path, bool strict = true)
    {
        var rule = AssetRule(asset);
        if (rule is null || path.Count == 0 || path.Count > MaxDepth || path.Any(p => p is null || p.Contains("{{") || p.Contains('\u001f'))) return null;
        var data = rule.Value;
        if (data.GetProperty("mode").GetString() == "paths")
        {
            foreach (var pattern in data.GetProperty("paths").EnumerateArray())
                if (Match(path, pattern, strict) is { } normalized) return normalized;
            return null;
        }
        bool Member(string property, string value) => Root.GetProperty(property).EnumerateArray().Any(p => p.GetString() == value);
        return data.GetProperty("mode").GetString() == "all" || Member("visible_fields", path[^1])
            || path[^1] == "Name" && Member("name_assets", data.GetProperty("name").GetString()!) ? path.ToArray() : null;
    }
    internal static string ListMode(string asset, IReadOnlyList<string> path)
    {
        var rule = AssetRule(asset);
        if (rule is null) return "skip";
        if (rule.Value.GetProperty("mode").GetString() != "paths") return "index";
        return rule.Value.GetProperty("id_lists").EnumerateArray().Any(pattern => Match(path, pattern) is not null) ? "id" : "skip";
    }
    internal static string[]? ListIds(string asset, IReadOnlyList<string> path, JsonElement[] children)
    {
        string mode = ListMode(asset, path);
        if (mode == "skip") return null;
        if (mode == "index") return Enumerable.Range(0, children.Length).Select(i => i.ToString(System.Globalization.CultureInfo.InvariantCulture)).ToArray();
        var ids = children.Select(child => child.ValueKind == JsonValueKind.Object
            ? child.EnumerateObject().FirstOrDefault(p => p.Name.Equals("Id", StringComparison.OrdinalIgnoreCase)).Value : default)
            .Select(key => key.ValueKind == JsonValueKind.String ? key.GetString() : null).ToArray();
        return ids.All(ValidId) && ids.Distinct(StringComparer.Ordinal).Count() == ids.Length ? ids.Select(id => id!).ToArray() : null;
    }
    internal static string ReferenceKind(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "unresolved";
        if (Full(value, Root.GetProperty("i18n_pattern").GetString()!, RegexOptions.IgnoreCase)) return "i18n";
        if (Full(value, LocalizedPattern)) return "localized";
        return value.Contains("{{") || value.Contains('[') ? "unresolved" : "literal";
    }
    internal static string UnrelatedLanguage(JsonElement change)
    {
        if (!change.TryGetProperty("Action", out var action) || action.ValueKind != JsonValueKind.String || !new[] { "Include", "EditData", "Load", "EditImage", "EditMap" }.Contains(action.GetString())) return "";
        bool Known(string value) => Root.GetProperty("known_languages").EnumerateArray().Any(p => p.GetString() == value);
        if (change.TryGetProperty("When", out var when) && when.ValueKind == JsonValueKind.Object
            && when.TryGetProperty("Language", out var language) && language.ValueKind == JsonValueKind.String)
        {
            string[] values = language.GetString()!.Split(',').Select(p => p.Trim()).ToArray();
            if (values.All(Known) && !values.Any(p => p is "en" or "zh")) return "When.Language=" + string.Join(',', values);
        }
        if (action.GetString() == "EditData" && change.TryGetProperty("TargetLocale", out var locale) && locale.ValueKind == JsonValueKind.String
            && locale.GetString() is { } code && Known(code) && code is not ("en" or "zh")) return "TargetLocale=" + code;
        return "";
    }
}

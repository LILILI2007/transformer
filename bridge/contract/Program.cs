using System.Text.Json;
using ModLingo.Bridge;

// Executes the production Bridge evaluator without SMAPI or a running game.
foreach (var item in JsonDocument.Parse(Console.In.ReadToEnd()).RootElement.EnumerateArray())
{
    string operation = item.GetProperty("op").GetString()!;
    string asset = item.TryGetProperty("asset", out var a) ? a.GetString()! : "";
    string[] path = item.TryGetProperty("path", out var p) ? p.EnumerateArray().Select(x => x.GetString()!).ToArray() : Array.Empty<string>();
    object? result = operation switch
    {
        "path" => ResourceRules.TextPath(asset, path),
        "asset" => ResourceRules.CanonicalAsset(asset),
        "list" => ResourceRules.ListIds(asset, path, item.GetProperty("children").EnumerateArray().ToArray()),
        "reference" => ResourceRules.ReferenceKind(item.GetProperty("value").ValueKind == JsonValueKind.String ? item.GetProperty("value").GetString() : null),
        "language" => ResourceRules.UnrelatedLanguage(item.GetProperty("change")),
        "identity" => new { version = ResourceRules.Version, hash = ResourceRules.Hash },
        _ => throw new InvalidOperationException(operation)
    };
    Console.WriteLine(JsonSerializer.Serialize(result));
}

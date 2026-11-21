using System.Collections;
using System.Collections.Concurrent;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Mono.Cecil;
using Mono.Cecil.Cil;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace ModLingo.Bridge;

public sealed class ModEntry : Mod
{
    private static ModEntry Instance = null!;
    private Harmony Patches = null!;
    private readonly ConcurrentDictionary<string, Capture> Captures = new();
    private readonly ConcurrentDictionary<string,(string Mod,string Context,string Source)> RegisteredLiterals=new();
    private readonly Dictionary<string, List<Translation>> Translations = new();
    private readonly Dictionary<string, Dictionary<string, Translation>> Lookup = new();
    private readonly ConcurrentDictionary<string,int> Applied = new();
    private readonly ConcurrentDictionary<string,object> AppliedUnique = new();
    private readonly ConcurrentDictionary<string,bool> GmcmCapabilities = new();
    private readonly string SessionId=Guid.NewGuid().ToString("N");
    private string GmcmVersion="unavailable";
    private List<DisplayAlias> DisplayAliases=new();
    private sealed class DisplayAlias { public string mod_id {get;set;}="";public string context {get;set;}="";public string source {get;set;}="";public string target {get;set;}=""; }
    private void TrackApplied(string adapter,string mod,string context,string source,string target)
    {
        string key=mod+"\0"+context+"\0"+Hash(source);
        if(AppliedUnique.TryAdd(key,new {adapter,mod_id=mod,context,source_hash=Hash(source),target_hash=Hash(target),mod_version=Mods.TryGetValue(mod,out var info)?info.Version:""}))
        {Applied.AddOrUpdate(adapter,1,(_,n)=>n+1);Interlocked.Exchange(ref Changed,1);}
    }
    private string Alias(string mod,string context,string source)
    {
        if(LocalizedContentManager.CurrentLanguageCode!=LocalizedContentManager.LanguageCode.zh)return source;
        var rule=DisplayAliases.FirstOrDefault(r=>r.mod_id==mod&&r.context==context&&r.source==source);
        if(rule is null)return source;
        TrackApplied("gmcm",mod,context,source,rule.target);
        return rule.target;
    }
    private readonly Dictionary<string, (string Version, string Directory)> Mods = new();
    private readonly Dictionary<string, HashSet<string>> Literals = new();
    private readonly Dictionary<string, HashSet<string>> AssetOwners = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, Dictionary<string, HashSet<string>>> AssetPaths = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> DynamicMinecartNames = new(StringComparer.Ordinal);
    private readonly Dictionary<string, List<Translation>> AssetTranslations = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, Dictionary<string, List<Translation>>> AssetTranslationPaths = new(StringComparer.OrdinalIgnoreCase);
    private readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = false, WriteIndented = true };
    [ThreadStatic] private static string? Owner;
    [ThreadStatic] private static string? Callsite;
    [ThreadStatic] private static bool Drawing;
    private int Changed;
    private int Flushing;
    private static int GmcmCalls;
    private static readonly ConditionalWeakTable<object,ChoiceFormatter> ChoiceFormatters=new();
    private Dictionary<string,NameAlias> NameAliases=new();
    private sealed class NameAlias { public string source {get;set;}="";public string target {get;set;}=""; }
    private sealed record ChoiceFormatter(Func<string,string>? Original,Func<string,string> Display);
    private sealed record RegisteredText(Func<string> Original,string Mod,string Context);
    private sealed record DisplayText(Func<string> Original,Func<string> Display);
    private sealed class TextAccessors { public Dictionary<string,DisplayText> Values=new(); }
    private sealed record PageOwner(string Mod,string Page);
    private static readonly ConditionalWeakTable<Delegate,RegisteredText> RegisteredTextDelegates=new();
    private static readonly ConditionalWeakTable<object,TextAccessors> DisplayAccessors=new();
    private static readonly ConditionalWeakTable<object,PageOwner> PageOwners=new();

    public override void Entry(IModHelper helper)
    {
        Instance = this;
        try
        {
            string names=Path.Combine(helper.DirectoryPath,"names.zh.json");
            if(File.Exists(names)&&new FileInfo(names).Length<1024*1024&&(File.GetAttributes(names)&FileAttributes.ReparsePoint)==0)
                NameAliases=JsonSerializer.Deserialize<Dictionary<string,NameAlias>>(File.ReadAllText(names))??new();
        }
        catch(Exception ex) when(ex is IOException or JsonException) { Monitor.Log("Local name aliases unavailable; original names kept.",LogLevel.Trace); }
        try
        {
            string rules=Path.Combine(helper.DirectoryPath,"display.zh.json");
            if(File.Exists(rules)&&new FileInfo(rules).Length<1024*1024&&(File.GetAttributes(rules)&FileAttributes.ReparsePoint)==0)
                DisplayAliases=JsonSerializer.Deserialize<List<DisplayAlias>>(File.ReadAllText(rules))??new();
        }
        catch(Exception ex) when(ex is IOException or JsonException) { Monitor.Log("Display aliases unavailable.",LogLevel.Warn); }
        Patches = new Harmony(ModManifest.UniqueID);
        PatchGmcm();
        PatchGraphics();
        helper.Events.GameLoop.GameLaunched += (_, _) =>
        {
            IndexMods(Directory.GetParent(Helper.DirectoryPath)!.FullName, 0);
            LoadCaptures();
            foreach(var pending in RegisteredLiterals.Values)
                CaptureText(pending.Mod,pending.Context,pending.Source,"gmcm","",Array.Empty<string>());
            RegisteredLiterals.Clear();
            LoadTranslations();
            foreach (var pair in AssetTranslations)
                AssetTranslationPaths[pair.Key] = pair.Value.GroupBy(t=>string.Join("\u001f",t.path)).ToDictionary(g=>g.Key,g=>g.ToList());
            PatchGmcm();
            IndexDrawingMethods();
        };
        helper.Events.Content.AssetRequested += OnAssetRequested;
        helper.Events.GameLoop.OneSecondUpdateTicked += (_, _) => Flush();
        helper.Events.GameLoop.ReturnedToTitle += (_, _) => Flush();
        helper.ConsoleCommands.Add("modlingo_flush", "Save captured text locally; no network.", (_, _) => Flush());
        helper.ConsoleCommands.Add("modlingo_status", "Show offline bridge status.", (_, _) => Monitor.Log($"ModLingo: {Captures.Count} captured, {Translations.Count} dictionaries; runtime verification pending.", LogLevel.Info));
        Monitor.Log("ModLingo：启动时自动采集静态设置文字，游玩时自动补充动态文字。静态文字无需逐项点击；退出游戏后在桌面程序补译。动态剧情仍需实际触发。", LogLevel.Info);
    }

    private void IndexMods(string directory, int depth)
    {
        if (depth > 12 || Mods.Count > 2000) return;
        foreach (string child in Directory.EnumerateDirectories(directory))
        {
            if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0) continue;
            string manifest = Path.Combine(child, "manifest.json");
            if (!File.Exists(manifest)) { IndexMods(child, depth + 1); continue; }
            try
            {
                using var data = JsonDocument.Parse(File.ReadAllText(manifest), new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
                string id = data.RootElement.GetProperty("UniqueID").GetString()!;
                if (id == ModManifest.UniqueID || Helper.ModRegistry.Get(id) is null) continue;
                string version = data.RootElement.GetProperty("Version").GetString()!;
                Mods[id] = (version, child);
                IndexAssets(id, child);
            }
            catch (Exception ex) when (ex is IOException or JsonException or KeyNotFoundException) { }
        }
    }

    private void IndexAssets(string id, string directory)
    {
        int count = 0;
        foreach (string file in SafeFiles(directory))
        {
            if (++count > 4096) break;
            if (!file.EndsWith(".json", StringComparison.OrdinalIgnoreCase) || file.Contains(Path.DirectorySeparatorChar + "i18n" + Path.DirectorySeparatorChar)) continue;
            if (new FileInfo(file).Length > 20 * 1024 * 1024) continue;
            try
            {
                using var data = JsonDocument.Parse(File.ReadAllText(file), new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true });
                void Walk(JsonElement node)
                {
                    if (node.ValueKind == JsonValueKind.Object)
                    {
                        if (ResourceRules.UnrelatedLanguage(node).Length>0) return;
                        if (node.TryGetProperty("Action",out var patchAction) && patchAction.ValueKind==JsonValueKind.String
                            && patchAction.GetString() is "EditData" or "Load"
                            && node.TryGetProperty("Target", out var target) && target.ValueKind == JsonValueKind.String)
                            foreach (string asset in target.GetString()!.Split(','))
                            {
                                string? canonical = ResourceRules.CanonicalAsset(asset.Trim());
                                if(canonical is null) continue;
                                string name=canonical;
                                if (!AssetOwners.TryGetValue(name, out var owners)) AssetOwners[name] = owners = new();
                                owners.Add(id);
                                if (!AssetPaths.TryGetValue(name,out var byMod)) AssetPaths[name]=byMod=new();
                                if (!byMod.TryGetValue(id,out var paths)) byMod[id]=paths=new();
                                void Leaves(JsonElement element,List<string> pointer)
                                {
                                    if(pointer.Count>ResourceRules.MaxDepth) return;
                                    if(element.ValueKind==JsonValueKind.Object)
                                        foreach(var property in element.EnumerateObject()) {pointer.Add(property.Name);Leaves(property.Value,pointer);pointer.RemoveAt(pointer.Count-1);}
                                    else if(element.ValueKind==JsonValueKind.Array)
                                    {
                                        var children=element.EnumerateArray().ToArray();
                                        var ids=ResourceRules.ListIds(name,pointer,children);
                                        if(ids is null)return;
                                        for(int i=0;i<children.Length;i++) {pointer.Add(ids[i]);Leaves(children[i],pointer);pointer.RemoveAt(pointer.Count-1);}
                                    }
                                    else if(element.ValueKind==JsonValueKind.String)
                                    {
                                        var normalized=ResourceRules.TextPath(name,pointer);
                                        if(name.Equals("Data/Minecarts",StringComparison.OrdinalIgnoreCase)&&normalized?.Last()=="DisplayName")
                                        {
                                            string source=element.GetString()??"";
                                            if(ResourceRules.ReferenceKind(source)=="unresolved")
                                                DynamicMinecartNames.Add(string.Join("\u001f",normalized));
                                        }
                                        if(normalized is not null) paths.Add(string.Join("\u001f",normalized));
                                    }
                                }
                                var scope=new List<string>();
                                bool scopeValid=true;
                                if(node.TryGetProperty("TargetField",out var targetField))
                                {
                                    if(targetField.ValueKind!=JsonValueKind.Array||targetField.GetArrayLength()>12) scopeValid=false;
                                    else foreach(var key in targetField.EnumerateArray())
                                    {
                                        string? part=key.ValueKind==JsonValueKind.String?key.GetString():key.ValueKind==JsonValueKind.Number&&key.TryGetInt64(out long number)?number.ToString(System.Globalization.CultureInfo.InvariantCulture):null;
                                        if(part is null||part.Contains("{{")||part.Contains('\u001f')) {scopeValid=false;break;}
                                        scope.Add(part);
                                    }
                                }
                                if(scopeValid)
                                {
                                    if(node.TryGetProperty("Entries",out var entries)) Leaves(entries,new(scope));
                                    if(node.TryGetProperty("Fields",out var fields)) Leaves(fields,new(scope));
                                }
                                if(node.TryGetProperty("Action",out var action)&&action.GetString()=="Load"&&node.TryGetProperty("FromFile",out var from))
                                {
                                    string? relative=from.GetString();
                                    if(relative?.EndsWith(".json")==true&&!relative.Contains("{{"))
                                    {
                                        string full=Path.GetFullPath(Path.Combine(directory,relative));
                                        if(full.StartsWith(Path.GetFullPath(directory)+Path.DirectorySeparatorChar)&&File.Exists(full)&&(File.GetAttributes(full)&FileAttributes.ReparsePoint)==0&&new FileInfo(full).Length<20*1024*1024)
                                        {
                                            using var loaded=JsonDocument.Parse(File.ReadAllText(full),new JsonDocumentOptions {CommentHandling=JsonCommentHandling.Skip,AllowTrailingCommas=true});
                                            Leaves(loaded.RootElement,new());
                                        }
                                    }
                                }
                            }
                        foreach (var p in node.EnumerateObject()) Walk(p.Value);
                    }
                    else if (node.ValueKind == JsonValueKind.Array) foreach (var element in node.EnumerateArray()) Walk(element);
                }
                Walk(data.RootElement);
            }
            catch (Exception ex) when (ex is IOException or JsonException) { }
        }
    }

    private static IEnumerable<string> SafeFiles(string directory)
    {
        var pending = new Stack<(string Path, int Depth)>();
        pending.Push((directory, 0));
        int visited = 0;
        while (pending.Count > 0 && visited++ < 20000)
        {
            var current = pending.Pop();
            foreach (string path in Directory.EnumerateFileSystemEntries(current.Path))
            {
                var attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.ReparsePoint) != 0) continue;
                if ((attributes & FileAttributes.Directory) != 0) { if (current.Depth < 12) pending.Push((path, current.Depth + 1)); }
                else yield return path;
            }
        }
    }

    private void LoadTranslations()
    {
        string root = Path.Combine(Helper.DirectoryPath, "translations");
        if (!Directory.Exists(root)) return;
        foreach (string path in Directory.EnumerateFiles(root, "*.json"))
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0 || new FileInfo(path).Length > 20 * 1024 * 1024) continue;
            try
            {
                var pack = JsonSerializer.Deserialize<TranslationPack>(File.ReadAllText(path), Json);
                if (pack is null || pack.schema_version != 1 || !Mods.TryGetValue(pack.mod_id, out var mod) || pack.mod_version != mod.Version) continue;
                var records = pack.records.Where(r => r.source_hash == Hash(r.source) && r.target.Length <= 200000).ToList();
                Translations[pack.mod_id] = records;
                Lookup[pack.mod_id]=records.GroupBy(r=>r.context+"\0"+r.source_hash).ToDictionary(g=>g.Key,g=>g.Last());
                foreach (var r in records.Where(r => r.adapter == "asset"))
                {
                    if (!AssetTranslations.TryGetValue(r.asset, out var values)) AssetTranslations[r.asset] = values = new();
                    values.Add(r);
                }
            }
            catch (Exception ex) when (ex is IOException or JsonException or NullReferenceException) { Monitor.Log("A bridge dictionary could not be read; original text kept.", LogLevel.Warn); }
        }
    }

    private void LoadCaptures()
    {
        string path = Path.Combine(Helper.DirectoryPath, "captures.json");
        if (!File.Exists(path) || new FileInfo(path).Length > 20 * 1024 * 1024 || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return;
        try
        {
            using var data = JsonDocument.Parse(File.ReadAllText(path));
            if (data.RootElement.GetProperty("schema_version").GetInt32() != 1) return;
            foreach (var element in data.RootElement.GetProperty("captures").EnumerateArray().Take(20000))
            {
                var r = element.Deserialize<Capture>(Json);
                if (r is not null && Mods.TryGetValue(r.mod_id, out var mod) && mod.Version == r.mod_version && r.source_hash == Hash(r.source))
                    Captures.TryAdd(r.mod_id + "\0" + r.context + "\0" + r.source_hash, r);
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or KeyNotFoundException) { }
    }

    private void OnAssetRequested(object? sender, AssetRequestedEventArgs e)
    {
        string? name = ResourceRules.CanonicalAsset(e.NameWithoutLocale.Name);
        if(name is null) return;
        AssetOwners.TryGetValue(name, out var owners);
        AssetTranslations.TryGetValue(name, out var records);
        if (owners is null && records is null) return;
        e.Edit(asset =>
        {
            try { WalkAsset(asset.Data, name, new List<string>(), owners, records, 0); }
            catch (Exception ex) { Monitor.Log("Bridge asset adapter skipped " + name + " (" + ex.GetType().Name + ").", LogLevel.Trace); }
        }, AssetEditPriority.Late);
    }

    private static string? LocalizedMinecartName(string source)
    {
        if(LocalizedContentManager.CurrentLanguageCode!=LocalizedContentManager.LanguageCode.zh)return null;
        // Only the documented built-in string reference. No arbitrary token
        // evaluation, config getters, custom names, or global language switch.
        var match=Regex.Match(source,@"\A(?:"+ResourceRules.LocalizedPattern+@")\z",RegexOptions.CultureInvariant);
        if(!match.Success)return null;
        try
        {
            string value=Game1.content.LoadString(match.Groups[1].Value);
            return Regex.IsMatch(value,@"[\u3400-\u9fff]")&&!value.Contains("[LocalizedText")&&!value.Contains("{{")?value:null;
        }
        catch(Exception) {return null;}
    }
    private void WalkAsset(object? value, string asset, List<string> path, HashSet<string>? owners, List<Translation>? translations, int depth)
    {
        if (value is null || depth > ResourceRules.MaxDepth) return;
        string Change(string source)
        {
            string[]? capturePath=ResourceRules.TextPath(asset,path);
            if(capturePath is null)return source;
            if(asset=="Data/Minecarts"&&DynamicMinecartNames.Contains(string.Join("\u001f",capturePath)))return source;
            string context = asset + "/" + string.Join("/", capturePath);
            if(AssetPaths.TryGetValue(asset,out var owned))
            {
                string pointer=string.Join("\u001f",capturePath);
                string[] ownedMatches=owned.Where(p=>p.Value.Contains(pointer)).Select(p=>p.Key).ToArray();
                if(ownedMatches.Length==1) CaptureText(ownedMatches[0],context,source,"asset",asset,capturePath,
                    asset=="Data/Minecarts"&&capturePath.Last()=="DisplayName"?LocalizedMinecartName(source):null);
            }
            if (LocalizedContentManager.CurrentLanguageCode != LocalizedContentManager.LanguageCode.zh || translations is null) return source;
            if(!AssetTranslationPaths.TryGetValue(asset,out var indexed)||!indexed.TryGetValue(string.Join("\u001f",capturePath),out var candidates)) return source;
            string hash=Hash(source);
            var matches = candidates.Where(t => t.source_hash == hash && t.source == source).Select(t => t.target).Distinct().ToArray();
            if(matches.Length==1&&matches[0]!=source)
                foreach(var match in candidates.Where(t=>t.source_hash==hash&&t.source==source))TrackApplied("asset",match.mod_id,match.context,source,matches[0]);
            return matches.Length == 1 ? matches[0] : source;
        }
        bool Visible() => ResourceRules.TextPath(asset,path) is not null;
        if (value is IDictionary dictionary)
        {
            foreach (object key in dictionary.Keys.Cast<object>().ToArray())
            {
                path.Add(key.ToString()!);
                object? child = dictionary[key];
                if (child is string source && Visible()) dictionary[key] = Change(source);
                else WalkAsset(child, asset, path, owners, translations, depth + 1);
                path.RemoveAt(path.Count - 1);
            }
        }
        else if (value is IList list)
        {
            string mode=ResourceRules.ListMode(asset,path);
            if(mode=="skip")return;
            string?[]? ids=null;
            if(mode=="id")
            {
                ids=list.Cast<object?>().Select(value=>value?.GetType().GetField("Id",BindingFlags.Public|BindingFlags.Instance|BindingFlags.IgnoreCase)?.GetValue(value) as string).ToArray();
                if(ids.Any(key=>!ResourceRules.ValidId(key))||ids.Distinct(StringComparer.Ordinal).Count()!=ids.Length)return;
            }
            for (int i = 0; i < list.Count; i++)
            {
                path.Add(ids is null?i.ToString():ids[i]!);
                if (list[i] is string source && Visible()) list[i] = Change(source);
                else WalkAsset(list[i], asset, path, owners, translations, depth + 1);
                path.RemoveAt(path.Count - 1);
            }
        }
        else if (!value.GetType().IsPrimitive && (value.GetType().Assembly == typeof(Game1).Assembly || value.GetType().Assembly.GetName().Name == "StardewValley.GameData"))
        {
            foreach (var property in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance).Where(p => p.CanRead && p.GetIndexParameters().Length == 0))
            {
                path.Add(property.Name);
                if (property.PropertyType == typeof(string) && property.CanWrite && Visible()) property.SetValue(value, Change((string?)property.GetValue(value) ?? ""));
                else if (property.PropertyType != typeof(string)) WalkAsset(property.GetValue(value), asset, path, owners, translations, depth + 1);
                path.RemoveAt(path.Count - 1);
            }
            foreach (var field in value.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
            {
                path.Add(field.Name);
                if (field.FieldType == typeof(string) && Visible()) field.SetValue(value, Change((string?)field.GetValue(value) ?? ""));
                else if (field.FieldType != typeof(string)) WalkAsset(field.GetValue(value), asset, path, owners, translations, depth + 1);
                path.RemoveAt(path.Count - 1);
            }
        }
    }

    private void PatchGmcm()
    {
        var assembly=AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a=>a.GetType("GenericModConfigMenu.Framework.Api") is not null);
        var type = assembly?.GetType("GenericModConfigMenu.Framework.Api");
        foreach(string capability in new[]{"name","tooltip","pages","page_title","mod_name","choices","numeric_int","numeric_float","registration"})GmcmCapabilities[capability]=false;
        GmcmVersion=assembly?.GetName().Version?.ToString()??"unavailable";
        Interlocked.Exchange(ref Changed,1);
        if (type is null) return;
        var optionType=assembly?.GetType("GenericModConfigMenu.Framework.ModOption.BaseModOption");
        foreach(string property in new[]{"Name","Tooltip"})
        {
            var getter=optionType?.GetProperty(property)?.GetMethod;
            GmcmCapabilities[property.ToLowerInvariant()]=getter is not null;
            if(getter is not null&&Harmony.GetPatchInfo(getter)?.Owners.Contains(ModManifest.UniqueID)!=true)
                Patches.Patch(getter,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmOptionText)));
        }
        var configType=assembly?.GetType("GenericModConfigMenu.Framework.ModConfig");
        var pagesGetter=configType?.GetProperty("Pages")?.GetMethod;
        if(pagesGetter is not null&&Harmony.GetPatchInfo(pagesGetter)?.Owners.Contains(ModManifest.UniqueID)!=true)
            Patches.Patch(pagesGetter,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmPages)));
        GmcmCapabilities["pages"]=pagesGetter is not null;
        var pageGetter=assembly?.GetType("GenericModConfigMenu.Framework.ModConfigPage")?.GetProperty("PageTitle")?.GetMethod;
        if(pageGetter is not null&&Harmony.GetPatchInfo(pageGetter)?.Owners.Contains(ModManifest.UniqueID)!=true)
            Patches.Patch(pageGetter,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmPageText)));
        GmcmCapabilities["page_title"]=pageGetter is not null;
        var nameGetter=assembly?.GetType("GenericModConfigMenu.Framework.ModConfig")?.GetProperty("ModName")?.GetMethod;
        if(nameGetter is not null && Harmony.GetPatchInfo(nameGetter)?.Owners.Contains(ModManifest.UniqueID)!=true)
            Patches.Patch(nameGetter,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmName)));
        GmcmCapabilities["mod_name"]=nameGetter is not null;
        var choice=assembly?.GetType("GenericModConfigMenu.Framework.ModOption.ChoiceModOption`1")?.MakeGenericType(typeof(string));
        var formatter=choice?.GetProperty("FormatChoice")?.GetMethod;
        if(formatter is not null && Harmony.GetPatchInfo(formatter)?.Owners.Contains(ModManifest.UniqueID)!=true)
            Patches.Patch(formatter,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmChoice)));
        GmcmCapabilities["choices"]=formatter is not null;
        foreach(var numberType in new[]{typeof(int),typeof(float)})
        {
            var numeric=assembly?.GetType("GenericModConfigMenu.Framework.ModOption.NumericModOption`1")?.MakeGenericType(numberType)?.GetMethod("FormatValue",Type.EmptyTypes);
            GmcmCapabilities[numberType==typeof(int)?"numeric_int":"numeric_float"]=numeric is not null;
            if(numeric is not null && Harmony.GetPatchInfo(numeric)?.Owners.Contains(ModManifest.UniqueID)!=true)
                Patches.Patch(numeric,postfix:new HarmonyMethod(typeof(ModEntry),nameof(GmcmNumeric)));
        }
        int patched=0;
        foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.Instance).Where(m => m.Name.StartsWith("Add") && m.GetParameters().Any(p => p.ParameterType == typeof(Func<string>))))
        {
            if (Harmony.GetPatchInfo(method)?.Owners.Contains(ModManifest.UniqueID) == true) continue;
            Patches.Patch(method, prefix: new HarmonyMethod(typeof(ModEntry), nameof(GmcmPrefix)));
            patched++;
        }
        GmcmCapabilities["registration"]=type.GetMethods(BindingFlags.Public|BindingFlags.Instance).Any(m=>m.Name.StartsWith("Add")&&m.GetParameters().Any(p=>p.ParameterType==typeof(Func<string>)));
        Monitor.Log("GMCM capabilities: "+string.Join(", ",GmcmCapabilities.Select(p=>p.Key+"="+p.Value)),GmcmCapabilities.Values.All(v=>v)?LogLevel.Trace:LogLevel.Warn);
    }

    private static void GmcmName(object __instance,ref string __result)
    {
        if(__instance.GetType().GetProperty("ModManifest")?.GetValue(__instance) is IManifest manifest)
        {
            string original=__result;
            __result=Instance.Replace(manifest.UniqueID,"gmcm/mod-name",__result,"gmcm");
            if(__result==original && LocalizedContentManager.CurrentLanguageCode==LocalizedContentManager.LanguageCode.zh
                && Instance.NameAliases.TryGetValue(manifest.UniqueID,out var name) && name.source==original)
                __result=name.target;
            if(__result!=original&&!__result.Contains(original,StringComparison.Ordinal))__result+="（"+original+"）";
            if(__result!=original)Instance.TrackApplied("gmcm",manifest.UniqueID,"gmcm/mod-name",original,__result);
        }
    }

    private static void GmcmPages(object __instance,object __result)
    {
        if(__result is not IDictionary pages||__instance.GetType().GetProperty("ModManifest")?.GetValue(__instance) is not IManifest manifest)return;
        foreach(DictionaryEntry page in pages)
            if(page.Value is object value&&page.Key is string key&&!PageOwners.TryGetValue(value,out _))PageOwners.Add(value,new PageOwner(manifest.UniqueID,key));
    }

    private static void GmcmOptionText(object __instance,MethodBase __originalMethod,ref Func<string>? __result)
    {
        if(__result is null)return;
        string slot=__originalMethod.Name;
        var cache=DisplayAccessors.GetValue(__instance,_=>new TextAccessors());
        if(cache.Values.TryGetValue(slot,out var prior)&&prior.Original==__result){__result=prior.Display;return;}
        var owner=__instance.GetType().GetProperty("Owner")?.GetValue(__instance);
        if(owner?.GetType().GetProperty("ModManifest")?.GetValue(owner) is not IManifest manifest)return;
        string option=__instance.GetType().Name.Split('`')[0];
        string parameter=slot.Substring(4).ToLowerInvariant();
        string context="gmcm/runtime/"+option+"/"+parameter;
        if(__instance.GetType().GetProperty("FieldId")?.GetValue(__instance) is string field&&!Guid.TryParse(field,out _))context+="/"+field;
        string? api=option switch {
            "ParagraphModOption"=>"AddParagraph","SectionTitleModOption"=>"AddSectionTitle",
            "SectionSubHeaderModOption"=>"AddSubHeader","PageLinkModOption"=>"AddPageLink",
            "ChoiceModOption"=>"AddTextOption","NumericModOption"=>"AddNumberOption",
            "SimpleModOption" when __instance.GetType().GetGenericArguments().FirstOrDefault()==typeof(bool)=>"AddBoolOption",
            "SimpleModOption" when __instance.GetType().GetGenericArguments().FirstOrDefault()==typeof(string)=>"AddTextOption",
            _=>null};
        string? legacy=api is null?null:"gmcm/"+api+"/"+(parameter=="name"&&option is "ParagraphModOption" or "SectionTitleModOption" or "SectionSubHeaderModOption" or "PageLinkModOption"?"text":parameter);
        __result=Instance.DisplayDelegate(__instance,slot,__result,manifest.UniqueID,context,legacy);
    }

    private static void GmcmPageText(object __instance,ref Func<string> __result)
    {
        if(!PageOwners.TryGetValue(__instance,out var page))return;
        __result=Instance.DisplayDelegate(__instance,"page",__result,page.Mod,"gmcm/runtime/pageTitle/"+page.Page,"gmcm/AddPage/pageTitle");
    }

    private Func<string> DisplayDelegate(object option,string slot,Func<string> original,string mod,string runtimeContext,string? legacyContext=null)
    {
        var cache=DisplayAccessors.GetValue(option,_=>new TextAccessors());
        if(cache.Values.TryGetValue(slot,out var previous)&&previous.Original==original)return previous.Display;
        string primary=runtimeContext;
        Func<string> callback=original;
        // Legacy API overloads can call another patched registration method.
        // Unwrap all our layers so one display read captures one source/context.
        for(int depth=0;depth<16&&RegisteredTextDelegates.TryGetValue(callback,out var registered)&&registered.Mod==mod;depth++)
        {callback=registered.Original;primary=registered.Context;}
        Func<string> display=()=>
        {
            string? source=callback();
            if(source is null)return null!;
            string sourceContext=primary;
            if(primary==runtimeContext&&legacyContext is not null&&Captures.ContainsKey(mod+"\0"+legacyContext+"\0"+Hash(source)))
                sourceContext=legacyContext;
            if(LocalizedContentManager.CurrentLanguageCode==LocalizedContentManager.LanguageCode.zh&&Lookup.TryGetValue(mod,out var records))
                // Some GMCM versions copy/wrap the supplied delegate. Reuse a
                // known API context only with this mod's exact source/hash.
                foreach(string context in new[]{sourceContext,runtimeContext,legacyContext}.OfType<string>().Distinct())
                    if(records.TryGetValue(context+"\0"+Hash(source),out var record)&&record.source==source)
                    {
                        CaptureText(mod,context,source,"gmcm","",Array.Empty<string>());
                        if(record.target!=source)TrackApplied("gmcm",mod,context,source,record.target);
                        return record.target;
                    }
            CaptureText(mod,sourceContext,source,"gmcm","",Array.Empty<string>());
            return Alias(mod,sourceContext,source);
        };
        cache.Values[slot]=new DisplayText(original,display);
        return display;
    }

    private static void GmcmChoice(object __instance,ref Func<string,string> __result)
    {
        if(ChoiceFormatters.TryGetValue(__instance,out var cached) && cached.Original==__result)
        { __result=cached.Display;return; }
        // GMCM precomputes some formatted labels at registration. Wrap its
        // runtime display getter too, after dictionaries and mod identities load.
        var type=__instance.GetType();
        var owner=type.GetProperty("Owner")?.GetValue(__instance);
        if(owner?.GetType().GetProperty("ModManifest")?.GetValue(owner) is not IManifest manifest
            || type.GetProperty("Choices")?.GetValue(__instance) is not string[] choices)return;
        string context="gmcm/AddTextOption/formatAllowedValue";
        if(type.GetProperty("FieldId")?.GetValue(__instance) is string field && !Guid.TryParse(field,out _))context+="/"+field;
        var permitted=new HashSet<string>(choices);
        // A missing formatter means GMCM displays the raw choice as its label.
        // Supply an identity formatter for display only; never change Choices.
        var supplied=__result;
        var original=supplied??(value=>value);
        __result=value=>
        {
            string shown=original(value);
            return permitted.Contains(value)?Instance.Replace(manifest.UniqueID,context,shown,"gmcm"):shown;
        };
        ChoiceFormatters.Remove(__instance);
        ChoiceFormatters.Add(__instance,new ChoiceFormatter(supplied,__result));
    }

    private static void GmcmNumeric(object __instance,ref string __result)
    {
        var type=__instance.GetType();
        var owner=type.GetProperty("Owner")?.GetValue(__instance);
        if(owner?.GetType().GetProperty("ModManifest")?.GetValue(owner) is not IManifest manifest || __result is null)return;
        string kind=type.GetGenericArguments().FirstOrDefault()==typeof(int)?"int":"float";
        string context="gmcm/AddNumberOption/formatValue/"+kind;
        if(type.GetProperty("FieldId")?.GetValue(__instance) is string field && !Guid.TryParse(field,out _))context+="/"+field;
        __result=Instance.ReplaceNumeric(manifest.UniqueID,context,__result);
    }

    private static void GmcmPrefix(MethodBase __originalMethod, object __instance, object[] __args)
    {
        var manifest = __args.OfType<IManifest>().FirstOrDefault() ?? __instance.GetType().GetField("mod",BindingFlags.NonPublic|BindingFlags.Instance)?.GetValue(__instance) as IManifest;
        if (manifest is null) return;
        Instance.RegisterLiteral(manifest.UniqueID,"gmcm/mod-name",manifest.Name);
        if (Interlocked.Increment(ref GmcmCalls)<=3) Instance.Monitor.Log("GMCM registration intercepted: "+__originalMethod.Name+" / "+manifest.UniqueID,LogLevel.Trace);
        var parameters = __originalMethod.GetParameters();
        for (int i = 0; i < parameters.Length; i++)
        {
            string name = parameters[i].Name ?? "";
            if(name=="formatValue")
            {
                string numericContext="gmcm/"+__originalMethod.Name+"/formatValue";
                int field=Array.FindIndex(parameters,p=>p.Name=="fieldId");
                string suffix=field>=0 && __args[field] is string id?"/"+id:"";
                if(__args[i] is Func<int,string> integer)
                    __args[i]=new Func<int,string>(value=>Instance.ReplaceNumeric(manifest.UniqueID,numericContext+"/int"+suffix,integer(value)));
                else if(__args[i] is Func<float,string> fractional)
                    __args[i]=new Func<float,string>(value=>Instance.ReplaceNumeric(manifest.UniqueID,numericContext+"/float"+suffix,fractional(value)));
                continue;
            }
            // Translate only dropdown display delegates. Never alter the raw
            // allowed values / getValue / setValue or capture free-form input.
            if(name=="formatAllowedValue")
            {
                int allowed=Array.FindIndex(parameters,p=>p.Name=="allowedValues");
                if(allowed>=0 && __args[allowed] is string[] options)
                {
                    var display=__args[i] as Func<string,string> ?? (value=>value);
                    string choiceContext="gmcm/"+__originalMethod.Name+"/formatAllowedValue";
                    int field=Array.FindIndex(parameters,p=>p.Name=="fieldId");
                    if(field>=0 && __args[field] is string fieldId)choiceContext+="/"+fieldId;
                    // Without a custom formatter, these are exactly the labels
                    // GMCM will display. Never evaluate a custom formatter here.
                    if(__args[i] is null)
                        foreach(string option in options)
                            Instance.RegisterLiteral(manifest.UniqueID,choiceContext,option);
                    var permitted=new HashSet<string>(options);
                    __args[i]=new Func<string,string>(value=>
                    {
                        string shown=display(value);
                        return permitted.Contains(value)?Instance.Replace(manifest.UniqueID,choiceContext,shown,"gmcm"):shown;
                    });
                }
                continue;
            }
            if (name is not ("name" or "tooltip" or "text" or "pageTitle") || __args[i] is not Func<string> original) continue;
            string context = "gmcm/" + __originalMethod.Name + "/" + name;
            // A literal-returning delegate can be captured without evaluating
            // arbitrary mod code before the menu/world is ready.
            string? literal=ReadStaticText(original);
            if(literal is not null)
                Instance.RegisterLiteral(manifest.UniqueID,context,literal);
            var wrapped=new Func<string>(() => {string? value=original();return value is null?null!:Instance.Replace(manifest.UniqueID,context,value,"gmcm");});
            RegisteredTextDelegates.Add(wrapped,new RegisteredText(original,manifest.UniqueID,context));
            __args[i]=wrapped;
        }
    }

    // A deliberately tiny IL recognizer, not a delegate interpreter. Read only
    // literals or an existing instance string field; calls, properties, static
    // fields (type initializers), branches and save-dependent code stay lazy.
    private static string? ReadStaticText(Func<string> text)
    {
        try
        {
            byte[]? il=text.Method.GetMethodBody()?.GetILAsByteArray();
            if(il is {Length:6} && il[0]==0x72 && il[5]==0x2a)
                return text.Method.Module.ResolveString(BitConverter.ToInt32(il,1));
            if(il is {Length:7} && il[0]==0x02 && il[1]==0x7b && il[6]==0x2a && text.Target is not null)
            {
                var field=text.Method.Module.ResolveField(BitConverter.ToInt32(il,2),text.Method.DeclaringType?.GetGenericArguments(),text.Method.GetGenericArguments());
                if(field is not null && !field.IsStatic && field.FieldType==typeof(string))
                    return field.GetValue(text.Target) as string;
            }
        }
        catch(Exception ex) when(ex is ArgumentException or InvalidOperationException or NotSupportedException or MemberAccessException or BadImageFormatException) { }
        return null;
    }

    private void RegisterLiteral(string mod,string context,string source)
    {
        if(Mods.ContainsKey(mod)){CaptureText(mod,context,source,"gmcm","",Array.Empty<string>());return;}
        if(RegisteredLiterals.Count>=20000 || source.Length is <2 or >200000)return;
        RegisteredLiterals.TryAdd(mod+"\0"+context+"\0"+Hash(source),(mod,context,source));
    }

    private string ReplaceNumeric(string mod,string context,string source)
    {
        const string marker="{{ML_NUMBER}}";
        if(!source.Any(c=>c is >= 'A' and <= 'Z' or >= 'a' and <= 'z'))return source;
        if(source.Contains(marker))return source;
        var numbers=Regex.Matches(source,@"[-+]?\d+(?:[.,]\d+)*");
        if(numbers.Count==0)return Replace(mod,context,source,"gmcm");
        // Multiple numeric fields require an explicit adapter; don't guess which
        // one represents the config value or create unbounded per-value records.
        if(numbers.Count!=1)return source;
        var number=numbers[0];
        string template=source.Substring(0,number.Index)+marker+source.Substring(number.Index+number.Length);
        string target=Replace(mod,context,template,"gmcm");
        if(target.Split(marker).Length!=2)return source;
        return target.Replace(marker,number.Value);
    }

    private void IndexDrawingMethods()
    {
        foreach (var pair in Mods)
        {
            foreach (string file in Directory.EnumerateFiles(pair.Value.Directory, "*.dll"))
            {
                if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0) continue;
                try
                {
                    using var assembly = AssemblyDefinition.ReadAssembly(file);
                    var runtime = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a => !a.IsDynamic && a.GetName().Name == assembly.Name.Name);
                    if (runtime is null) continue;
                    if (!Literals.TryGetValue(pair.Key, out var literals)) Literals[pair.Key] = literals = new();
                    IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> roots) => roots.SelectMany(t => new[] { t }.Concat(Types(t.NestedTypes)));
                    foreach (var method in Types(assembly.MainModule.Types).SelectMany(t => t.Methods).Where(m => m.HasBody))
                    {
                        if (method.FullName.Contains("Chat", StringComparison.OrdinalIgnoreCase) || method.FullName.Contains("TextBox", StringComparison.OrdinalIgnoreCase)) continue;
                        foreach (var instruction in method.Body.Instructions.Where(i => i.OpCode == OpCodes.Ldstr)) literals.Add((string)instruction.Operand);
                        if (!method.Body.Instructions.Any(i => i.Operand is MethodReference m && (m.DeclaringType.Name == "SpriteBatch" || m.DeclaringType.Name == "SpriteText") && m.Name.Contains("draw", StringComparison.OrdinalIgnoreCase))) continue;
                        var target = runtime.ManifestModule.ResolveMethod(method.MetadataToken.ToInt32());
                        if (target is null || target.ContainsGenericParameters || Harmony.GetPatchInfo(target)?.Owners.Contains(ModManifest.UniqueID) == true) continue;
                        Patches.Patch(target, prefix: new HarmonyMethod(typeof(ModEntry), nameof(DrawingScope)), finalizer: new HarmonyMethod(typeof(ModEntry), nameof(DrawingEnd)));
                    }
                }
                catch (Exception ex) { Monitor.Log("A DLL drawing adapter was skipped (" + ex.GetType().Name + ").", LogLevel.Trace); }
            }
        }
    }

    private static void DrawingScope(MethodBase __originalMethod, out (string?, string?) __state)
    {
        __state = (Owner, Callsite);
        string location = __originalMethod.Module.Assembly.Location;
        var mod = Instance.Mods.FirstOrDefault(p => Path.GetDirectoryName(location) == p.Value.Directory);
        Owner = mod.Key;
        Callsite = "draw/" + __originalMethod.Module.ModuleVersionId + "/" + __originalMethod.MetadataToken;
    }
    private static void DrawingEnd((string?, string?) __state) { Owner = __state.Item1; Callsite = __state.Item2; }

    private void PatchGraphics()
    {
        foreach (Type type in new[] { typeof(SpriteBatch), typeof(StardewValley.BellsAndWhistles.SpriteText) })
            foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance).Where(m => m.Name.Contains("drawString", StringComparison.OrdinalIgnoreCase) && m.GetParameters().Any(p => p.ParameterType == typeof(string) || p.ParameterType == typeof(StringBuilder))))
                Patches.Patch(method, prefix: new HarmonyMethod(typeof(ModEntry), nameof(DrawPrefix)));
        foreach (var method in typeof(SpriteFont).GetMethods().Where(m => m.Name == "MeasureString" && m.GetParameters().Length == 1))
            Patches.Patch(method, prefix: new HarmonyMethod(typeof(ModEntry), nameof(MeasurePrefix)));
    }

    private static string RenderText(string source)
    {
        if (Drawing || Owner is null || Callsite is null || !Instance.Literals.TryGetValue(Owner, out var literals) || !literals.Contains(source)) return source;
        return Instance.Replace(Owner, Callsite, source, "draw");
    }
    private static void DrawPrefix(MethodBase __originalMethod, object[] __args)
    {
        var parameters = __originalMethod.GetParameters();
        for (int i = 0; i < parameters.Length; i++)
            if (__args[i] is string text || __args[i] is StringBuilder)
            {
                string source = __args[i].ToString()!;
                string target = RenderText(source);
                if (target == source) continue;
                __args[i] = parameters[i].ParameterType == typeof(StringBuilder) ? new StringBuilder(target) : target;
                for (int n = 0; n < parameters.Length; n++) if (__args[n] is SpriteFont font && target.Any(c => !font.Characters.Contains(c)) && Game1.smallFont is not null) __args[n] = Game1.smallFont;
            }
    }
    private static bool MeasurePrefix(SpriteFont __instance, object[] __args, ref Vector2 __result)
    {
        if (__args.Length == 0 || Drawing) return true;
        string source = __args[0].ToString()!;
        string target = RenderText(source);
        if (target == source) return true;
        try
        {
            Drawing = true;
            var font = target.Any(c => !__instance.Characters.Contains(c)) ? Game1.smallFont : __instance;
            __result = font.MeasureString(target);
            return false;
        }
        finally { Drawing = false; }
    }

    private string Replace(string mod, string context, string source, string adapter)
    {
        CaptureText(mod, context, source, adapter, "", Array.Empty<string>());
        if (LocalizedContentManager.CurrentLanguageCode != LocalizedContentManager.LanguageCode.zh || !Lookup.TryGetValue(mod, out var records)) return Alias(mod,context,source);
        if(records.TryGetValue(context+"\0"+Hash(source),out var record)&&record.source==source)
        {
            if(record.target!=source) TrackApplied(adapter,mod,context,source,record.target);
            return record.target;
        }
        return Alias(mod,context,source);
    }
    private void CaptureText(string mod, string context, string source, string adapter, string asset, string[] path, string? resolvedName=null)
    {
        if (source.Length is < 2 or > 200000 || !source.Any(c => c is >= 'A' and <= 'Z' or >= 'a' and <= 'z') || !Mods.TryGetValue(mod, out var info) || Captures.Count >= 20000) return;
        string hash = Hash(source);
        string key=mod+"\0"+context+"\0"+hash;
        var capture=new Capture { mod_id=mod, mod_version=info.Version, context=context, source=source, source_hash=hash, adapter=adapter, asset=asset, path=path,
            resolved_name=resolvedName,resolved_hash=resolvedName is null?null:Hash(resolvedName),locale=resolvedName is null?null:"zh" };
        if(Captures.TryAdd(key,capture))Interlocked.Exchange(ref Changed,1);
        else if(resolvedName is not null&&Captures.TryGetValue(key,out var previous)&&previous.resolved_name!=resolvedName)
        {Captures[key]=capture;Interlocked.Exchange(ref Changed,1);}
    }
    private void Flush()
    {
        if (Interlocked.CompareExchange(ref Flushing, 1, 0) != 0) return;
        if (Interlocked.Exchange(ref Changed, 0) == 0) { Interlocked.Exchange(ref Flushing, 0); return; }
        var snapshot = Captures.Values.ToArray();
        var unique=AppliedUnique.Values.ToArray();
        var capabilities=GmcmCapabilities.ToDictionary(p=>p.Key,p=>p.Value);
        Task.Run(() =>
        {
            try
            {
                string path = Path.Combine(Helper.DirectoryPath, "captures.json");
                string temporary = path + ".tmp";
                File.WriteAllText(temporary, JsonSerializer.Serialize(new { schema_version=1, captures=snapshot, applied=Applied.ToDictionary(p=>p.Key,p=>p.Value), applied_unique=unique, session_id=SessionId, bridge_version=ModManifest.Version.ToString(), extraction_rules_version=ResourceRules.Version, extraction_rules_hash=ResourceRules.Hash, gmcm_version=GmcmVersion, gmcm_capabilities=capabilities }, Json), new UTF8Encoding(false));
                File.Move(temporary, path, true);
            }
            catch (IOException) { Interlocked.Exchange(ref Changed, 1); }
            finally { Interlocked.Exchange(ref Flushing, 0); }
        });
    }
    public static string Hash(string value) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();
}

public class Capture
{
    public string mod_id {get;set;}=""; public string mod_version {get;set;}="";
    public string context {get;set;}=""; public string source {get;set;}=""; public string source_hash {get;set;}="";
    public string adapter {get;set;}=""; public string asset {get;set;}=""; public string[] path {get;set;}=Array.Empty<string>();
    [System.Text.Json.Serialization.JsonIgnore(Condition=System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull)]
    public string? resolved_name {get;set;}
    [System.Text.Json.Serialization.JsonIgnore(Condition=System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull)]
    public string? resolved_hash {get;set;}
    [System.Text.Json.Serialization.JsonIgnore(Condition=System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull)]
    public string? locale {get;set;}
}
public class Translation : Capture { public string target {get;set;}=""; }
public class TranslationPack
{
    public int schema_version {get;set;} public string mod_id {get;set;}=""; public string mod_version {get;set;}="";
    public List<Translation> records {get;set;}=new();
}

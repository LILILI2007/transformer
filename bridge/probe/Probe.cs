using System.Text.Json;
using System.Reflection;
using System.Collections;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewValley;

public interface IProbeGmcm
{
    void Register(IManifest mod,Action reset,Action save,bool titleScreenOnly=false);
    void AddParagraph(IManifest mod,Func<string> text);
    void OpenModMenu(IManifest mod);
    void AddTextOption(IManifest mod,Func<string> getValue,Action<string> setValue,Func<string> name,Func<string>? tooltip=null,string[]? allowedValues=null,Func<string,string>? formatAllowedValue=null,string? fieldId=null);
    void AddNumberOption(IManifest mod,Func<int> getValue,Action<int> setValue,Func<string> name,Func<string>? tooltip=null,int? min=null,int? max=null,int? interval=null,Func<int,string>? formatValue=null,string? fieldId=null);
}
public sealed class Probe : Mod
{
    private int Seconds;
    private bool AssetPassed;
    private bool TargetFieldPassed;
    private bool EventResourcePassed;
    private bool HeaderResourcePassed;
    private bool WorldMapPassed,WorldMapDuplicateGuardPassed;
    private bool ClosingResourcePassed,ClosingArgumentsPassed;
    private bool BubbleResourcePassed,BubbleCommandPassed;
    private bool LanguageScopePassed;
    private bool MinecartPassed,MinecartPrivatePassed,MinecartLocalizedPassed;
    private string? BubbleError;
    private bool DrawRan;
    private string? Error;
    private IProbeGmcm? Menu;
    private string? MenuType;
    private string Choice="always";
    private bool NamePassed,ChoicePassed,RawPassed,BuiltinNamePassed,NumericPassed;
    private int Days=7;
    private string? NumericFirst,NumericSecond;
    private bool AnonymousChoicePassed;
    private bool RuntimeTextPassed,RuntimeDeferredPassed,RuntimeNullPassed,RuntimePagePassed,RuntimeRawPassed,RuntimeCachePassed;
    private bool StartupCapturePassed,StartupNoCallsPassed;
    public override void Entry(IModHelper helper)
    {
        // Bridge loads before this dependent fixture. Reproduce an early GMCM
        // literal arriving before the GameLaunched mod index exists, without
        // executing an arbitrary UI delegate or accessing player input.
        var bridge=AppDomain.CurrentDomain.GetAssemblies().Select(a=>a.GetType("ModLingo.Bridge.ModEntry")).First(t=>t is not null)!;
        var instance=bridge.GetField("Instance",BindingFlags.Static|BindingFlags.NonPublic)!.GetValue(null);
        bridge.GetMethod("RegisterLiteral",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(instance,new object[]{ModManifest.UniqueID,"gmcm/deferred-literal","Early registered literal"});
        helper.Events.GameLoop.GameLaunched += (_,_) =>
        {
            try
            {
                Menu=helper.ModRegistry.GetApi<IProbeGmcm>("spacechase0.GenericModConfigMenu");
                Menu?.Register(ModManifest,()=>{},()=>{},true);
                Menu?.AddParagraph(ModManifest,()=>"Hello GMCM fixture");
                Menu?.AddParagraph(ModManifest,()=>"Untranslated paragraph fixture");
                Menu?.AddTextOption(ModManifest,()=>Choice,value=>Choice=value,()=>"Visibility",allowedValues:new[]{"always","never"},formatAllowedValue:value=>value=="always"?"Always show":value=="never"?"Never show":value,fieldId:"probe-choice");
                Menu?.AddTextOption(ModManifest,()=>"seasonal",_=>{},()=>"Season",allowedValues:new[]{"seasonal"},formatAllowedValue:_=>"Seasonal mode");
                Menu?.AddNumberOption(ModManifest,()=>Days,value=>Days=value,()=>"Delay",min:1,max:100,formatValue:value=>value+" days",fieldId:"probe-days");
                string capturedLabel="Startup captured field label";
                Menu?.AddTextOption(ModManifest,()=>"Startup first choice",_=>{},()=>capturedLabel,allowedValues:new[]{"Startup first choice","Startup second choice"},fieldId:"startup-raw");
                var captures=bridge.GetField("Captures",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(instance)!;
                var values=(IEnumerable)captures.GetType().GetProperty("Values")!.GetValue(captures)!;
                var sources=values.Cast<object>().Select(v=>(string?)v.GetType().GetProperty("source")!.GetValue(v)).ToHashSet();
                StartupCapturePassed=new[]{capturedLabel,"Startup first choice","Startup second choice"}.All(sources.Contains);
                int invocations=0;
                Func<string> deferred=()=>{invocations++;throw new InvalidOperationException("Requires save");};
                var inspect=bridge.GetMethod("ReadStaticText",BindingFlags.Static|BindingFlags.NonPublic)!;
                StartupNoCallsPassed=inspect.Invoke(null,new object[]{deferred}) is null && invocations==0;
                int formatterCalls=0;
                Menu?.AddTextOption(ModManifest,()=>"custom",_=>{},()=>"Custom startup choice",allowedValues:new[]{"custom"},formatAllowedValue:value=>{formatterCalls++;return "Custom display";},fieldId:"startup-custom");
                StartupNoCallsPassed &= formatterCalls==0;
                Func<string> mutable=()=>capturedLabel;
                capturedLabel="Updated startup field label";
                StartupNoCallsPassed &= mutable()==capturedLabel;
            }
            catch(Exception ex){Error=ex.ToString();}
        };
        helper.Events.Display.Rendered += (_,_)=>DrawFixture();
        helper.Events.GameLoop.OneSecondUpdateTicked += (_,_)=>
        {
            Seconds++;
            if(Seconds==4)
            {
                try
                {
                    if(!StartupCapturePassed || !StartupNoCallsPassed)throw new InvalidOperationException("Automatic startup capture or lazy delegate guard failed");
                    LocalizedContentManager.CurrentLanguageCode=LocalizedContentManager.LanguageCode.zh;
                    var data=Helper.GameContent.Load<Dictionary<string,string>>("Characters/Dialogue/Abigail");
                    AssetPassed=data.TryGetValue("ModLingoProbe",out string? value)&&value=="你好资源测试";
                    var events=Helper.GameContent.Load<Dictionary<string,string>>("Data/Events/Town");
                    EventResourcePassed=events.TryGetValue("ModLingoProbeQuestion",out string? eventText)&&eventText=="pause 1/quickQuestion 帮帮我？#好的#不了(break)message \"谢谢你！\"\\pause 100(break)switchEvent KeepBranchID/end";
                    HeaderResourcePassed=events.TryGetValue("ModLingoProbeHeader",out string? headerText)&&headerText=="none/-100 -100/farmer 12 12 1/message \"你好剧情头部测试！\"/end";
                    ClosingResourcePassed=events.TryGetValue("ModLingoProbeClosing",out string? closingText)&&closingText=="pause 1/end dialogue Abigail \"明天见！\"";
                    if(ClosingResourcePassed)
                    {
                        // Parse arguments only: executing End could warp/save.
                        string[] args=ArgUtility.SplitBySpaceQuoteAware(closingText!.Substring("pause 1/".Length));
                        ClosingArgumentsPassed=args.Length==4&&args[0]=="end"&&args[1]=="dialogue"&&args[2]=="Abigail"&&args[3]=="明天见！";
                    }
                    BubbleResourcePassed=events.TryGetValue("ModLingoProbeBubble",out string? bubbleText)&&bubbleText=="pause 1/textAboveHead ModLingoBubbleActor \"你好气泡！\"/end";
                    if(BubbleResourcePassed)
                    {
                        try { BubbleCommandPassed=CheckBubbleCommand(bubbleText!.Split('/')[1]); }
                        catch(Exception ex) { BubbleError=ex.ToString(); }
                    }
                    var objects=Helper.GameContent.Load<Dictionary<string,StardewValley.GameData.Objects.ObjectData>>("Data/Objects");
                    var maps=Helper.GameContent.Load<Dictionary<string,StardewValley.GameData.WorldMaps.WorldMapRegionData>>("Data/WorldMap");
                    var region=maps["ModLingoProbeRegion"];
                    WorldMapPassed=region.MapAreas.Count==2&&region.MapAreas[0].Id=="second"&&region.MapAreas[0].ScrollText=="Probe map scroll"&&region.MapAreas[1].Id=="area"&&region.MapAreas[1].ScrollText=="中文地图区域"&&region.MapAreas[1].PixelArea==new Rectangle(11,22,33,44)&&region.MapAreas[1].Tooltips[0].Id=="tip"&&region.MapAreas[1].Tooltips[0].Text=="中文地图提示"&&region.MapAreas[1].Tooltips[0].Condition=="TRUE"&&region.MapAreas[1].Tooltips[0].LeftNeighbor=="Neighbor";
                    var duplicate=new Dictionary<string,StardewValley.GameData.WorldMaps.WorldMapRegionData>{["ModLingoProbeRegion"]=new(){MapAreas=new(){new(){Id="area",ScrollText="Probe map scroll"},new(){Id="area",ScrollText="Probe map scroll"}}}};
                    var translations=(IDictionary)bridge.GetField("AssetTranslations",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(instance)!;
                    bridge.GetMethod("WalkAsset",BindingFlags.Instance|BindingFlags.NonPublic)!.Invoke(instance,new object?[]{duplicate,"Data/WorldMap",new List<string>(),null,translations["Data/WorldMap"],0});
                    WorldMapDuplicateGuardPassed=duplicate["ModLingoProbeRegion"].MapAreas.All(a=>a.ScrollText=="Probe map scroll");
                    TargetFieldPassed=objects.TryGetValue("ModLingoProbeObject",out var obj)&&obj.DisplayName=="嵌套路径花朵"&&obj.Description=="嵌套路径说明"&&obj.Name=="probe_internal_id"&&obj.Price==123;
                    var carts=Helper.GameContent.Load<Dictionary<string,StardewValley.GameData.Minecarts.MinecartNetworkData>>("Data/Minecarts");
                    var network=carts["ModLingoProbeNetwork"];
                    var dock=network.Destinations.Single(d=>d.Id=="ModLingoProbe.Dock");
                    MinecartPassed=network.LockedMessage=="矿车尚未开放"&&dock.DisplayName=="测试码头"&&dock.Id=="ModLingoProbe.Dock"
                        &&dock.TargetLocation=="Town"&&dock.Price==123&&dock.CustomFields?.GetValueOrDefault("DisplayName")=="Internal display sentinel";
                    MinecartPrivatePassed=network.Destinations.Single(d=>d.Id=="ModLingoProbe.Custom").DisplayName=="Private minecart sentinel";
                    var captureStore=bridge.GetField("Captures",BindingFlags.Instance|BindingFlags.NonPublic)!.GetValue(instance)!;
                    var nameCaptures=(IEnumerable)captureStore.GetType().GetProperty("Values")!.GetValue(captureStore)!;
                    MinecartPrivatePassed&=!nameCaptures.Cast<object>().Any(v=>(string?)v.GetType().GetProperty("source")!.GetValue(v)=="Private minecart sentinel");
                    string nativeBus=Game1.content.LoadString("Strings\\Locations:MineCart_Destination_BusStop");
                    MinecartLocalizedPassed=nativeBus.Any(c=>c is >= '\u3400' and <= '\u9fff')&&nameCaptures.Cast<object>().Any(v=>(string?)v.GetType().GetProperty("resolved_name")!.GetValue(v)==nativeBus
                        &&(string?)v.GetType().GetProperty("locale")!.GetValue(v)=="zh");
                    var gmcm=AppDomain.CurrentDomain.GetAssemblies().First(a=>a.GetType("GenericModConfigMenu.Mod") is not null);
                    var type=gmcm.GetType("GenericModConfigMenu.Mod")!;
                    const BindingFlags flags=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.Static;
                    var mod=type.GetField("instance",flags)!.GetValue(null);
                    var manager=type.GetField("ConfigManager",flags)!.GetValue(mod)!;
                    var config=manager.GetType().GetMethod("Get")!.Invoke(manager,new object[]{ModManifest,false})!;
                    CheckRuntimeText(config,gmcm);
                    NamePassed=(string?)config.GetType().GetProperty("ModName")!.GetValue(config)=="模组语言集成探针（ModLingo integration probe）";
                    var own=Activator.CreateInstance(config.GetType(),new object[]{Helper.ModRegistry.Get("spacechase0.GenericModConfigMenu")!.Manifest,(Action)(()=>{}),(Action)(()=>{}),true})!;
                    BuiltinNamePassed=(string?)config.GetType().GetProperty("ModName")!.GetValue(own)=="模组配置菜单（GMCM）（Generic Mod Config Menu）";
                    foreach(var option in (IEnumerable)config.GetType().GetMethod("GetAllOptions")!.Invoke(config,null)!)
                    {
                        if(option.GetType().Name.StartsWith("NumericModOption") && option.GetType().GetMethod("FormatValue",Type.EmptyTypes) is MethodInfo numeric)
                        {
                            option.GetType().GetProperty("Value")!.SetValue(option,7);
                            string? first=NumericFirst=(string?)numeric.Invoke(option,null);
                            Days=9;
                            option.GetType().GetProperty("Value")!.SetValue(option,9);
                            string? second=NumericSecond=(string?)numeric.Invoke(option,null);
                            NumericPassed=first=="7 天"&&second=="9 天"&&Days==9;
                            Days=7;
                        }
                        if(option.GetType().GetProperty("FormatChoice")?.GetValue(option) is Func<string,string> format)
                        {
                            if((string?)option.GetType().GetProperty("FieldId")?.GetValue(option)=="probe-choice")
                            {
                                ChoicePassed=format("always")=="始终显示";
                                RawPassed=Choice=="always" && format("private input sentinel")=="private input sentinel";
                            }
                            else if(((string[]?)option.GetType().GetProperty("Choices")?.GetValue(option))?.Contains("seasonal")==true) AnonymousChoicePassed=format("seasonal")=="季节模式";
                        }
                    }
                    Menu?.OpenModMenu(ModManifest);
                }
                catch(Exception ex){Error=ex.ToString();}
            }
            if(Seconds==6)
            {
                try
                {
                    Helper.GameContent.InvalidateCache("Characters/Dialogue/Abigail");
                    var localized=Helper.GameContent.Load<Dictionary<string,string>>("Characters/Dialogue/Abigail");
                    LanguageScopePassed=LocalizedContentManager.CurrentLanguageCode==LocalizedContentManager.LanguageCode.zh&&localized.GetValueOrDefault("ModLingoProbeLanguage")=="中文条件基础"&&localized.GetValueOrDefault("ModLingoProbeNativeZh")=="作者已有中文";
                }
                catch(Exception ex){Error=ex.ToString();}
            }
            if(Seconds==6&&File.Exists(Path.Combine(Helper.DirectoryPath,"real-menus.json")))
            {
                try { CheckRealMenus(); } catch(Exception ex){Error=ex.ToString();}
            }
            if(Seconds>=8&&Seconds<=12)
            {
                try { Menu?.OpenModMenu(ModManifest); } catch(Exception ex){Error=ex.ToString();}
                MenuType=Game1.activeClickableMenu?.GetType().FullName+" / "+typeof(StardewValley.Menus.TitleMenu).GetField("subMenu",System.Reflection.BindingFlags.Public|System.Reflection.BindingFlags.Static)?.GetValue(null)?.GetType().FullName;
            }
            if(Seconds>=15)
            {
                File.WriteAllText(Path.Combine(Helper.DirectoryPath,"probe-result.json"),JsonSerializer.Serialize(new {startup_capture_checked=StartupCapturePassed,startup_no_calls_checked=StartupNoCallsPassed,gmcm_runtime_text_checked=RuntimeTextPassed,gmcm_runtime_deferred_checked=RuntimeDeferredPassed,gmcm_runtime_null_checked=RuntimeNullPassed,gmcm_runtime_page_checked=RuntimePagePassed,gmcm_runtime_raw_checked=RuntimeRawPassed,gmcm_runtime_cache_checked=RuntimeCachePassed,world_map_checked=WorldMapPassed,world_map_duplicate_guard_checked=WorldMapDuplicateGuardPassed,closing_resource_checked=ClosingResourcePassed,closing_arguments_checked=ClosingArgumentsPassed,header_resource_checked=HeaderResourcePassed,asset_checked=AssetPassed,language_scope_checked=LanguageScopePassed,target_field_checked=TargetFieldPassed,event_resource_checked=EventResourcePassed,bubble_resource_checked=BubbleResourcePassed,bubble_command_checked=BubbleCommandPassed,bubble_error=BubbleError,draw_ran=DrawRan,gmcm_registered=Menu is not null,gmcm_name_checked=NamePassed,gmcm_builtin_name_checked=BuiltinNamePassed,gmcm_numeric_checked=NumericPassed,numeric_first=NumericFirst,numeric_second=NumericSecond,gmcm_choice_checked=ChoicePassed,gmcm_anonymous_choice_checked=AnonymousChoicePassed,raw_choice_unchanged=RawPassed,menu_type=MenuType,error=Error}));
                File.WriteAllText(Path.Combine(Helper.DirectoryPath,"minecart-probe-result.json"),JsonSerializer.Serialize(new {
                    minecart_resource_checked=MinecartPassed,minecart_private_name_ignored=MinecartPrivatePassed,minecart_localized_name_checked=MinecartLocalizedPassed}));
                Game1.game1.Exit();
            }
        };
    }
    private void CheckRealMenus()
    {
        // Only these three reviewed Content Patcher packs, on the title screen.
        // No setters, save callbacks or arbitrary mod delegates are executed.
        var requested=JsonSerializer.Deserialize<string[]>(File.ReadAllText(Path.Combine(Helper.DirectoryPath,"real-menus.json")))!;
        var allowed=new[]{"recon88.NoPamEnabling","7eventy7.StandardizedSeedSprites","jibb.minecarts"};
        var rows=new List<object>();
        var gmcm=AppDomain.CurrentDomain.GetAssemblies().First(a=>a.GetType("GenericModConfigMenu.Mod") is not null);
        const BindingFlags flags=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.Static;
        var type=gmcm.GetType("GenericModConfigMenu.Mod")!;
        var mod=type.GetField("instance",flags)!.GetValue(null);
        var manager=type.GetField("ConfigManager",flags)!.GetValue(mod)!;
        foreach(string id in requested.Where(allowed.Contains))
        {
            var info=Helper.ModRegistry.Get(id);
            if(info is null){rows.Add(new {mod_id=id,error="not loaded"});continue;}
            Menu!.OpenModMenu(info.Manifest);
            var config=manager.GetType().GetMethod("Get")!.Invoke(manager,new object[]{info.Manifest,false});
            if(config is null){rows.Add(new {mod_id=id,error="menu not registered"});continue;}
            var options=new List<object>();
            foreach(var option in (IEnumerable)config.GetType().GetMethod("GetAllOptions")!.Invoke(config,null)!)
            {
                var optionType=option.GetType();
                string? name=(optionType.GetProperty("Name")?.GetValue(option) as Func<string>)?.Invoke();
                string? tooltip=(optionType.GetProperty("Tooltip")?.GetValue(option) as Func<string>)?.Invoke();
                var choices=optionType.GetProperty("Choices")?.GetValue(option) as string[];
                var formatter=optionType.GetProperty("FormatChoice")?.GetValue(option) as Func<string,string>;
                var shown=choices?.Select(raw=>new {raw,display=formatter?.Invoke(raw)??raw}).ToArray();
                options.Add(new {type=optionType.Name,name,tooltip,choices=shown});
            }
            rows.Add(new {mod_id=id,title=config.GetType().GetProperty("ModName")?.GetValue(config),options});
        }
        File.WriteAllText(Path.Combine(Helper.DirectoryPath,"real-menu-results.json"),JsonSerializer.Serialize(rows));
    }
    private void CheckRuntimeText(object config,Assembly gmcm)
    {
        // Construct only our fixture's options through GMCM's own constructors.
        // This reproduces objects registered before the API prefix was attached.
        var simple=gmcm.GetType("GenericModConfigMenu.Framework.ModOption.SimpleModOption`1")!.MakeGenericType(typeof(bool));
        int calls=0;bool raw=false;
        Func<string> label=()=>{calls++;return "DebugMode_ForceNewQuestLetter";};
        var option=Activator.CreateInstance(simple,new object?[]{"probe-runtime-bool",label,(Func<string>)(()=>null!),config,(Func<bool>)(()=>raw),(Action<bool>)(v=>raw=v)})!;
        config.GetType().GetMethod("AddOption")!.Invoke(config,new[]{option});
        var getter=simple.GetProperty("Name")!;
        var shown=(Func<string>)getter.GetValue(option)!;
        RuntimeDeferredPassed=calls==0;
        RuntimeTextPassed=shown()=="调试：强制新任务信";
        RuntimeCachePassed=ReferenceEquals(shown,getter.GetValue(option));
        RuntimeNullPassed=((Func<string>)simple.GetProperty("Tooltip")!.GetValue(option)!)() is null;
        simple.GetProperty("Value")!.SetValue(option,true);
        simple.GetMethod("BeforeSave")!.Invoke(option,null);
        RuntimeRawPassed=raw&&(string?)simple.GetProperty("FieldId")!.GetValue(option)=="probe-runtime-bool";
        var sectionType=gmcm.GetType("GenericModConfigMenu.Framework.ModOption.SectionTitleModOption")!;
        var section=Activator.CreateInstance(sectionType,new object?[]{(Func<string>)(()=>"Mod Section Toggles"),null,config})!;
        config.GetType().GetMethod("AddOption")!.Invoke(config,new[]{section});
        RuntimeTextPassed&=((Func<string>)sectionType.GetProperty("Name")!.GetValue(section)!)()=="模组分组开关";
        var choiceType=gmcm.GetType("GenericModConfigMenu.Framework.ModOption.ChoiceModOption`1")!.MakeGenericType(typeof(string));
        string destination="jibb.Farm";
        var choice=Activator.CreateInstance(choiceType,new object?[]{"probe-runtime-choice",(Func<string>)(()=>"QuestLetterFrom"),(Func<string>)(()=>"Choose who sends the quest letter"),config,(Func<string>)(()=>destination),(Action<string>)(v=>destination=v),new[]{"jibb.Farm","Bus"},null})!;
        config.GetType().GetMethod("AddOption")!.Invoke(config,new[]{choice});
        var format=(Func<string,string>)choiceType.GetProperty("FormatChoice")!.GetValue(choice)!;
        RuntimeCachePassed&=ReferenceEquals(format,choiceType.GetProperty("FormatChoice")!.GetValue(choice));
        RuntimeTextPassed&=format("jibb.Farm")=="农场"&&format("Bus")=="巴士站"&&((Func<string>)choiceType.GetProperty("Name")!.GetValue(choice)!)()=="任务信寄件人"&&((Func<string>)choiceType.GetProperty("Tooltip")!.GetValue(choice)!)()=="选择任务信的寄件人";
        RuntimeRawPassed&=destination=="jibb.Farm"&&format("private input sentinel")=="private input sentinel"&&((string[])choiceType.GetProperty("Choices")!.GetValue(choice)!).SequenceEqual(new[]{"jibb.Farm","Bus"});
        // Exercise GMCM's own selection/save path on our in-memory fixture.
        choiceType.GetProperty("Value")!.SetValue(choice,"Bus");
        choiceType.GetMethod("BeforeSave")!.Invoke(choice,null);
        RuntimeRawPassed&=destination=="Bus"&&format(destination)=="巴士站"
            &&(string?)choiceType.GetProperty("FieldId")!.GetValue(choice)=="probe-runtime-choice";
        config.GetType().GetMethod("SetActiveRegisteringPage")!.Invoke(config,new object?[]{"Advanced Settings",null});
        var pages=(IDictionary)config.GetType().GetProperty("Pages")!.GetValue(config)!;
        var page=pages["Advanced Settings"]!;
        RuntimePagePassed=((Func<string>)page.GetType().GetProperty("PageTitle")!.GetValue(page)!)()=="高级设置"&&(string?)page.GetType().GetProperty("PageId")!.GetValue(page)=="Advanced Settings";
        config.GetType().GetMethod("SetActiveRegisteringPage")!.Invoke(config,new object?[]{"",null});
    }
    private static bool CheckBubbleCommand(string script)
    {
        // Own in-memory event and actor only. Never add to a game location or
        // set Game1.currentLocation/currentEvent; no save or world is opened.
        var evt=new Event();
        var actor=new NPC {Name="ModLingoBubbleActor"};
        const BindingFlags flags=BindingFlags.Public|BindingFlags.NonPublic|BindingFlags.Instance|BindingFlags.Static;
        var actors=(IList?)(typeof(Event).GetField("actors",flags)?.GetValue(evt)??typeof(Event).GetProperty("actors",flags)?.GetValue(evt))
            ??throw new InvalidOperationException("Event actors unavailable: "+string.Join(",",typeof(Event).GetMembers(flags).Where(m=>m.Name.Contains("actor",StringComparison.OrdinalIgnoreCase)).Select(m=>m.Name)));
        actors.Add(actor);
        string[] args=ArgUtility.SplitBySpaceQuoteAware(script);
        var context=new EventContext(evt,new GameLocation(),new GameTime(),args);
        var handler=typeof(Event).GetNestedType("DefaultCommands",flags)?.GetMethod("TextAboveHead",flags)
            ??throw new InvalidOperationException("TextAboveHead command handler unavailable");
        handler.Invoke(null,new object[]{evt,args,context});
        for(Type? type=actor.GetType();type is not null;type=type.BaseType)
        {
            var field=type.GetField("textAboveHead",flags);
            if(field is not null)return field.GetValue(actor)?.ToString()=="你好气泡！";
        }
        throw new InvalidOperationException("NPC bubble state unavailable: "+string.Join(",",actor.GetType().GetMembers(flags).Where(m=>m.Name.Contains("textAbove",StringComparison.OrdinalIgnoreCase)).Select(m=>m.Name)));
    }
    private void DrawFixture()
    {
        if(Seconds<4||Game1.smallFont is null)return;
        // Isolated fixture only; draw at an off-screen position, not over gameplay.
        using var batch=new SpriteBatch(Game1.graphics.GraphicsDevice);
        batch.Begin();
        batch.DrawString(Game1.smallFont,"Hello drawing fixture",new Vector2(-2000,-2000),Color.White);
        batch.End();
        DrawRan=true;
    }
}

-- AIPlays 客户端：读对话界面（NPC 台词、选项、检定），写给调度器；按调度器的指令选选项。
-- 文件（都在 Script Extender 文件夹）：
--   AIPlays_dialog.json      客户端 -> 调度器：当前对话状态（有变化才写）
--   AIPlays_dialog_cmd.json  调度器 -> 客户端：{session, seq, answer_idx, text}
-- 也可以不重启游戏热加载（客户端控制台）：
--   Ext.UI.Defer(function() load(Ext.IO.LoadFile("AIPlays_client.lua"))() end)

AIPC = AIPC or {}
local C = AIPC
C.STATE_FILE = "AIPlays_dialog.json"
C.CMD_FILE = "AIPlays_dialog_cmd.json"
C.POLL_SEC = 0.5
C.SESSION = C.SESSION or tostring(math.floor(Ext.Utils.MonotonicTime()))
C.seq = C.seq or 0
C.lastJson = nil
C.lastCmdSeq = C.lastCmdSeq or -1
C.lastCmd = C.lastCmd or nil
C.nextPoll = 0
C.nextFind = 0
C.widget, C.body = nil, nil
C.busy = false

local function now() return Ext.Utils.MonotonicTime() / 1000 end
local function get(o, k)
  if rawequal(o, nil) then return nil end
  local ok, v = pcall(function() return o[k] end)
  if ok then return v end
end
local function len(o)
  local ok, n = pcall(function() return #o end)
  if ok and type(n) == "number" then return n end
  return 0
end

-- 界面对象只在一次 Defer 回调里有效，不能跨回调缓存。
-- 所以缓存的是“路径”（每层第几个子节点），每次按路径重新走一遍，对不上再全树搜索。
local function childAt(o, i)
  local ok, c = pcall(function() return o:VisualChild(i) end)
  if ok then return c end
end

local function findByName(o, names, found, depth, path)
  if rawequal(o, nil) or depth > 60 then return end
  local n = get(o, "Name")
  if n and names[n] and not found[n] then
    local copy = {}
    for i, v in ipairs(path) do copy[i] = v end
    found[n] = copy
  end
  if found.Dialogue and found.TextBodyContainer then return end   -- 都找到了就不再往下搜
  local cnt = get(o, "VisualChildrenCount") or 0
  for i = 1, cnt do
    local c = childAt(o, i)
    if c then
      path[#path + 1] = i
      findByName(c, names, found, depth + 1, path)
      path[#path] = nil
      if found.Dialogue and found.TextBodyContainer then return end
    end
  end
end

local function follow(root, path, name)
  if not path then return nil end
  local o = root
  for _, i in ipairs(path) do
    o = childAt(o, i)
    if rawequal(o, nil) then return nil end
  end
  if get(o, "Name") == name then return o end
end

-- 找两个界面节点：Dialogue（有 SelectAnswerCommand）和 TextBodyContainer（台词 + 选项）
local function nodes()
  local root = Ext.UI.GetRoot()
  local w = follow(root, C.widgetPath, "Dialogue")
  local b = follow(root, C.bodyPath, "TextBodyContainer")
  if w and b then return w, b end
  if now() < C.nextFind then return nil end
  C.nextFind = now() + 3
  local found = {}
  pcall(findByName, root, { Dialogue = true, TextBodyContainer = true }, found, 0, {})
  C.widgetPath, C.bodyPath = found.Dialogue, found.TextBodyContainer
  w = follow(root, C.widgetPath, "Dialogue")
  b = follow(root, C.bodyPath, "TextBodyContainer")
  if w and b then return w, b end
end

local function loca(handle)
  if type(handle) ~= "string" or handle == "" then return nil end
  local ok, s = pcall(Ext.Loca.GetTranslatedString, handle)
  if ok and s and s ~= "" then return s end
end

local function entityName(uuid)
  if not uuid or uuid == "" then return nil end
  local ok, s = pcall(function()
    local dn = Ext.Entity.Get(uuid).DisplayName
    local name = get(dn, "Name")
    if (not name or name == "") then
      local key = get(dn, "NameKey")
      local okg, g = pcall(function() return key:Get() end)
      if okg then name = g end
      if (not name or name == "") then name = loca(get(get(key, "Handle"), "Handle")) end
    end
    return name
  end)
  if ok then return s end
end

-- 选项上的检定 / 标签（比如 [说服]、[吉斯洋基人]）
local function answerTags(a)
  local tags, seen = {}, {}
  local function add(s)
    s = tostring(s or "")
    if s ~= "" and not seen[s] then seen[s] = true; tags[#tags + 1] = s end
  end
  local params = get(get(a, "CtxAnswer"), "Params")
  for i = 1, len(params) do
    local p = get(params, i)
    local typ = tostring(get(p, "Type") or "")
    if typ ~= "" and typ ~= "Text" then add(typ .. ":" .. tostring(get(p, "Text") or "")) end
    local dts = get(p, "DiceTypeSet")
    local dice = get(dts, "DiceType")
    if dice and tostring(dice) ~= "None" then add("dice:" .. tostring(dice) .. " " .. tostring(get(dts, "Str") or "")) end
    if len(get(p, "Rolls")) > 0 then add("roll") end
  end
  local at = get(a, "AnswerTags")
  for i = 1, len(at) do add(get(at, i)) end
  return tags
end

local function readDialog()
  local widget, body = nodes()
  if not widget then return { active = false } end
  local vm = get(body, "DataContext")
  if not vm then return { active = false } end
  local visible = get(widget, "IsVisible")
  local showAnswers = get(vm, "ShowAnswers") == true
  local showSub = get(vm, "ShowSubtitle") == true
  local line = get(vm, "BodyText") or ""
  local st = {
    active = visible ~= false and (showAnswers or showSub) and true or false,
    instance = get(vm, "DialogInstanceID"),
    npc = loca(get(vm, "SpeakerName")) or "",
    line = line,
    show_answers = showAnswers,
    answers = {},
  }
  local speakers = get(vm, "AvailableSpeakers")
  local sp = get(speakers, 1)
  for i = 1, len(speakers) do
    local s = get(speakers, i)
    if get(s, "IsSelected") == true then sp = s break end
  end
  -- 真正在说话的我方角色：优先用对话界面的 CurrentSpeakerCharacter（比如你用盖尔对话时就是盖尔），
  -- 读不到再退回 AvailableSpeakers
  local cur = get(get(widget, "DataContext"), "CurrentSpeakerCharacter")
  local curUuid = get(cur, "EntityUUID")
  if curUuid and curUuid ~= "" then
    st.speaker_uuid = curUuid
    st.speaker = entityName(curUuid)
    st.speaker_source = "current"
  elseif sp then
    st.speaker_uuid = get(sp, "EntityUUID")
    st.speaker = entityName(st.speaker_uuid)
    st.speaker_source = "available"
  end
  if showAnswers then
    local answers = get(vm, "Answers")
    for i = 1, len(answers) do
      local a = get(answers, i)
      if a then
        st.answers[#st.answers + 1] = {
          idx = get(a, "AnswerIdx"),
          slot = tonumber(tostring(get(a, "BoundEvent") or ""):match("(%d+)$")),
          text = get(a, "BodyText") or "",
          enabled = get(a, "Enabled") ~= false,
          tags = answerTags(a),
        }
      end
    end
  end
  return st
end

local function selectAnswer(cmd)
  local widget, body = nodes()
  if not widget then return false, "dialog UI not found" end
  local dc, vm = get(widget, "DataContext"), get(body, "DataContext")
  local answers = get(vm, "Answers")
  for i = 1, len(answers) do
    local a = get(answers, i)
    if get(a, "AnswerIdx") == cmd.answer_idx then
      if get(a, "Enabled") == false then return false, "answer disabled" end
      local command = get(dc, "SelectAnswerCommand")
      local ok, err = pcall(function() return command:Execute(a) end)
      return ok, ok and nil or tostring(err)
    end
  end
  return false, "answer not found"
end

-- 把界面里的值统一变成 JSON 能存的类型（TranslatedString 之类的转成文字）
local function plain(v, depth)
  local t = type(v)
  if t == "string" or t == "number" or t == "boolean" or t == "nil" then return v end
  if t == "table" then
    if (depth or 0) > 6 then return nil end
    local out = {}
    for k, x in pairs(v) do out[k] = plain(x, (depth or 0) + 1) end
    return out
  end
  local ok, g = pcall(function() return v:Get() end)
  if ok and type(g) == "string" then return g end
  local okh, h = pcall(function() return v.Handle.Handle end)
  if okh and type(h) == "string" then return loca(h) or "" end
  local oks, str = pcall(tostring, v)
  return oks and str or ""
end

-- ===================== 掷骰界面 / 战斗日志 =====================
-- 这两个都是顶层界面（和 Dialogue 同一层），直接在那一层按名字找，很便宜
local function topWidget(name)
  local root = Ext.UI.GetRoot()
  local cont = root
  for _, i in ipairs({ 1, 1, 1 }) do
    cont = childAt(cont, i)
    if rawequal(cont, nil) then return nil end
  end
  local n = get(cont, "VisualChildrenCount") or 0
  for i = n, 1, -1 do
    local c = childAt(cont, i)
    if c and get(c, "Name") == name then return c end
  end
end

local function str(v) return plain(v) end

local function readRoll()
  local w = topWidget("ActiveRoll")
  if not w or get(w, "IsVisible") == false then return nil end
  local dc = get(w, "DataContext")
  if not dc then return nil end
  local src = get(dc, "SourceObject")
  return {
    state = tostring(get(dc, "RollState") or ""),        -- WaitForStart / ... / ResultReady
    context = tostring(get(dc, "RollContext") or ""),    -- DialogCheck / ...
    dc = get(dc, "TargetNumber"),
    d20 = get(dc, "RolledNumber1"),
    d20b = get(dc, "RolledNumber2"),                     -- 优势 / 劣势时的第二颗
    total = get(dc, "FinalResult"),
    success = get(dc, "Success"),
    skill = loca(str(get(dc, "SkillOrAbility"))) or str(get(dc, "SkillOrAbility")),
    check = loca(str(get(dc, "AbilityCheckText"))) or str(get(dc, "AbilityCheckText")),
    result_text = loca(str(get(dc, "SkillOrAbilityResultText"))),
    line = str(get(dc, "SelectedDialogueLine")),
    inspiration = get(dc, "InspirationPoints"),
    roller = entityName(get(src, "EntityUUID")),
    roller_uuid = get(src, "EntityUUID"),
  }
end

local function runCommand(dc, names)
  for _, n in ipairs(names) do
    local cmd = get(dc, n)
    if cmd then
      local okC, can = pcall(function() return cmd:CanExecute(nil) end)
      if not okC or can ~= false then
        local ok, err = pcall(function() return cmd:Execute(nil) end)
        if ok then return true, n end
        _P("[AIPlays] " .. n .. " 执行失败：" .. tostring(err))
      end
    end
  end
  return false, "no command worked: " .. table.concat(names, "/")
end

local function rollAction(action)
  local w = topWidget("ActiveRoll")
  local dc = get(w, "DataContext")
  if not dc then return false, "roll UI not found" end
  if action == "roll" then
    return runCommand(dc, { "UserTriggeredNextPhaseCommand", "TriggerNextPhaseCommand" })
  elseif action == "reroll" then
    return runCommand(dc, { "RollAgainCommand", "TryAgainCommand" })
  else
    return runCommand(dc, { "ContinueCommand", "UserTriggeredNextPhaseCommand", "TriggerNextPhaseCommand" })
  end
end

-- 战斗日志：把 "[2]被命中，受到了[3]。" 这种模板里的 [n] 换成参数文字
local function renderCtx(ctx)
  local text = str(get(ctx, "Text"))
  if type(text) ~= "string" or text == "" then return nil end
  local params = get(ctx, "Params")
  return (text:gsub("%[(%d+)%]", function(n)
    local p = get(params, tonumber(n))
    local t = str(get(p, "Text"))
    return type(t) == "string" and t or ""
  end))
end

local function groupInfo(g)
  local ts = 0
  local entries = get(g, "Entries")
  for i = 1, len(entries) do
    local t = get(get(entries, i), "Timestamp")
    if type(t) == "number" and t > ts then ts = t end
  end
  local parts = {}
  local main = renderCtx(get(g, "CtxTransText"))
  if main then parts[#parts + 1] = main end
  -- 参数上的提示（命中检定的 AC、骰子）
  local params = get(get(g, "CtxTransText"), "Params")
  for i = 1, len(params) do
    local tips = get(get(params, i), "TooltipEntries")
    for k = 1, len(tips) do
      local t = renderCtx(get(get(tips, k), "CtxTransText"))
      if t and t ~= "" then parts[#parts + 1] = "(" .. tostring(get(get(tips, k), "Type") or "") .. ": " .. t .. ")" end
    end
  end
  for i = 1, len(entries) do
    local t = renderCtx(get(get(entries, i), "CtxTransText"))
    if t and t ~= main then parts[#parts + 1] = t end
  end
  return ts, table.concat(parts, " ")
end

C.logSeen = C.logSeen or nil
C.logLines = C.logLines or {}
C.logSeq = C.logSeq or 0
local function readCombatLog()
  local w, _ = nodes()
  local dc = get(w, "DataContext")
  if not dc then
    local cl = topWidget("CombatLog")
    dc = get(cl, "DataContext")
  end
  local groups = get(get(get(dc, "Data"), "CombatLog"), "EntryGroups")
  local n = len(groups)
  if n == 0 then return end
  -- 没变化就不重新扫（条目数、首尾两组的子条目数都一样）
  local sig = n .. ":" .. len(get(get(groups, 1), "Entries")) .. ":" .. len(get(get(groups, n), "Entries"))
  if sig == C.logSig then return end
  C.logSig = sig
  local fresh = {}
  local maxTs = C.logSeen or 0
  for i = 1, n do
    local g = get(groups, i)
    local ok, ts, text = pcall(groupInfo, g)
    if ok and ts > (C.logSeen or 0) and text ~= "" then
      fresh[#fresh + 1] = { t = ts, text = text }
    end
    if ok and ts > maxTs then maxTs = ts end
  end
  if C.logSeen == nil then C.logSeen = maxTs return end   -- 第一次：只记位置，不导出旧日志
  C.logSeen = maxTs
  if #fresh == 0 then return end
  table.sort(fresh, function(a, b) return a.t < b.t end)
  for _, f in ipairs(fresh) do
    table.insert(C.logLines, f)
    while #C.logLines > 60 do table.remove(C.logLines, 1) end
  end
  C.logSeq = C.logSeq + 1
  Ext.IO.SaveFile("AIPlays_combatlog.json",
    Ext.Json.Stringify({ session = C.SESSION, seq = C.logSeq, lines = C.logLines }))
end

local lastErr
local function report(err)
  err = tostring(err)
  if err ~= lastErr then lastErr = err; _P("[AIPlays] 对话读取出错：" .. err) end
end

local function poll()
  -- 1. 指令
  local raw = Ext.IO.LoadFile(C.CMD_FILE)
  if raw and raw ~= "" then
    local ok, cmd = pcall(Ext.Json.Parse, raw)
    if ok and type(cmd) == "table" and cmd.session == C.SESSION and type(cmd.seq) == "number"
        and cmd.seq > C.lastCmdSeq then
      C.lastCmdSeq = cmd.seq
      if cmd.roll_action then
        local okR, info = rollAction(cmd.roll_action)
        C.lastCmd = { seq = cmd.seq, ok = okR, err = okR and nil or info, used = okR and info or nil }
        if not okR then _P("[AIPlays] 掷骰界面 " .. cmd.roll_action .. " 失败：" .. tostring(info)) end
      else
        local okS, err = selectAnswer(cmd)
        C.lastCmd = { seq = cmd.seq, ok = okS, err = err }
        if not okS then _P("[AIPlays] 选择选项失败：" .. tostring(err)) end
      end
    end
  end
  -- 2. 状态（有变化才写）
  local st = plain(readDialog())
  local okR, roll = pcall(readRoll)
  st.roll = okR and plain(roll) or nil
  st.type = "dialog"
  st.session = C.SESSION
  st.last_cmd = C.lastCmd
  local body = Ext.Json.Stringify(st)
  if body ~= C.lastJson then
    C.lastJson = body
    C.seq = C.seq + 1
    st.seq = C.seq
    Ext.IO.SaveFile(C.STATE_FILE, Ext.Json.Stringify(st))
  end
  -- 3. 战斗日志（每 1.5 秒看一次，只在有新条目时写）
  C.logTick = (C.logTick or 0) + 1
  if C.logTick % 3 == 0 then
    local okL, errL = pcall(readCombatLog)
    if not okL then report(errL) end
  end
end

function C.onTick()
  if now() < C.nextPoll then return end
  if C.busy and now() - C.busy < 5 then return end   -- 上一次还没跑完（加载画面时 UI 不更新）
  C.nextPoll = now() + C.POLL_SEC
  C.busy = now()
  Ext.UI.Defer(function()
    local ok, err = pcall(poll)
    if not ok then report(err) end   -- 同样的错误只打印一次
    C.busy = false
  end)
end

if C.tickId then pcall(function() Ext.Events.Tick:Unsubscribe(C.tickId) end) end
C.tickId = Ext.Events.Tick:Subscribe(function() C.onTick() end)
_P("[AIPlays] 客户端对话模块已加载，session = " .. C.SESSION)

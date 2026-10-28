-- AIPlays: 让外部 AI 控制队伍成员战斗
-- 工作方式：
--   1. 队伍成员回合开始 -> 把战场状态写入 AIPlays_state.json
--   2. Python 调度器读取状态、询问 AI，把指令写入 AIPlays_command.json
--   3. 本脚本轮询指令文件，执行动作；动作结束后重新导出状态，直到 AI 结束回合
-- 两个文件都在 %LocalAppData%\Larian Studios\Baldur's Gate 3\Script Extender\ 下
--
-- 资源规则：Osi.UseSpell 本身不扣行动点，所以本 mod 自己记账：
--   读取技能的 UseCosts（例如 "ActionPoint:1;SpellSlotsGroup:1:1:1"），资源不够的技能不给 AI；
--   使用时从角色身上扣除（界面会同步变化），同时保留一份内部账本兜底。
--
-- 控制台命令（先输入 server 切到服务端）：
--   !aiplays on      开启 AI 控制（默认开启）
--   !aiplays off     关闭 AI 控制，回到手动操作
--   !aiplays status  查看当前状态
--   !aiplays reload  热加载 Script Extender 目录里的 AIPlays_patch.lua（不用重启游戏）
--
-- 所有函数都放在 AIP 表里，补丁文件可以直接覆盖任意 AIP.xxx。

AIP = AIP or {}
local A = AIP

A.STATE_FILE = "AIPlays_state.json"
A.CMD_FILE = "AIPlays_command.json"
A.PATCH_FILE = "AIPlays_patch.lua"
A.ROSTER_FILE = "AIPlays_roster.json"   -- 调度器写的角色分配：哪些角色归玩家操作

-- 逻辑里只用本地 CONFIG；AIP.CONFIG 指向同一张表，补丁可以改它的字段（比如 AIP.CONFIG.enforce_costs = false）
local CONFIG = {
    enabled = true,
    max_actions_per_turn = 8,     -- 防止 AI 卡在一个回合里无限行动
    turn_start_delay_ms = 1000,   -- 回合开始后等多久再导出状态（等镜头和 UI 就位）
    action_timeout_ms = 9000,     -- 等不到“施法完成/移动完成”事件时，最多等多久就继续
    settle_ms = 1200,             -- 动作完成后再等一会儿（让伤害数字、动画收尾）
    poll_every_ticks = 10,        -- 每隔多少帧读一次指令文件
    max_unit_distance = 60,       -- 只导出这个距离（米）以内的单位
    enforce_costs = true,         -- 按技能消耗记账（关掉 = 开挂模式）
    object_radius = 25,           -- 导出多远以内可互动的物体（门、拉杆、控制台……）
    max_objects = 24,             -- 导出给调度器的近处物体数（调度器再滤掉椅子、灯之类的装饰）
    landmark_radius = 120,        -- 更远的物体：每种名字取最近的一个，调度器按剧情（比如“魔舵”）挑出来给 AI
    fail_fast_ms = 3500,
    verbose = false,
    require_director = true,      -- 调度器没开 / 暂停时，AI 角色交给玩家操作（不会卡住等指令）
    heartbeat_ms = 6000,          -- 调度器心跳文件这么久没变化，就当它没开              -- true = 日志也打印到控制台（会刷屏）；!aiplays verbose 切换          -- 技能发出后这么久既没开始施放、角色也没动：当作没放出来
    -- 这些技能要瞄准物品或地面，AI 只能指定单位，用了也放不出来，干脆不给 AI 看
    hidden_spells = { Throw_Throw = true, Throw_ImprovisedWeapon = true, Target_Dip = true,
                      Projectile_Jump = true, Shout_Dash = false },
}
A.CONFIG = CONFIG

-- 每次加载 Lua 生成一个会话 ID，Python 端用它区分重启前后的序号
A.SESSION = A.SESSION or tostring(Ext.Utils.MonotonicTime())

A.rt = A.rt or {
    seq = 0,
    pending = nil,         -- 正在等待指令的 {seq, actor}
    currentActor = nil,    -- 当前由 AI 控制、正在行动的角色
    actionsThisTurn = 0,
    lastResult = nil,      -- 上一个动作的执行结果，回传给 AI
    scheduled = {},        -- 延迟执行的任务 {at, fn}
    tickCount = 0,
    ledger = {},           -- 本回合资源账本：key -> {start=, spent=}
}
A.rt.deferred = A.rt.deferred or {}   -- 等施法完成后才从角色身上扣的资源：{actor, spell, costs}
A.rt.waiting = A.rt.waiting or nil    -- 正在等待完成的动作：{actor, kind, spell, token}
A.rt.token = A.rt.token or 0
A.rt.turnQueue = A.rt.turnQueue or {}   -- 共享回合时，排队等待 AI 接管的角色

----------------------------------------------------------------------
-- 工具
----------------------------------------------------------------------
-- 日志同时写进 Script Extender 文件夹的 AIPlays_log.txt（最近 400 行），方便排查
A.logLines = A.logLines or {}
function A.log(...)
    -- 控制台只在 verbose 打开、或者正在执行控制台命令时显示；日志文件总是写
    if (A.CONFIG and A.CONFIG.verbose) or A.forcePrint then Ext.Utils.Print("[AIPlays]", ...) end
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
    local t = math.floor(Ext.Utils.MonotonicTime() / 100) / 10
    table.insert(A.logLines, string.format("%.1f ", t) .. table.concat(parts, " "))
    while #A.logLines > 400 do table.remove(A.logLines, 1) end
    pcall(Ext.IO.SaveFile, "AIPlays_log.txt", table.concat(A.logLines, "\n"))
end

function A.now()
    return Ext.Utils.MonotonicTime()
end

-- 安全调用：游戏 API 出错时返回 nil，不让整个 mod 崩掉
function A.safe(fn, ...)
    if fn == nil then return nil end
    local r = table.pack(pcall(fn, ...))
    if r[1] then
        return table.unpack(r, 2, r.n)
    end
    return nil
end
local safe = function(...) return A.safe(...) end

function A.schedule(delayMs, fn)
    table.insert(A.rt.scheduled, { at = A.now() + delayMs, fn = fn })
end

function A.round(v, digits)
    if type(v) ~= "number" then return nil end
    local m = 10 ^ (digits or 1)
    return math.floor(v * m + 0.5) / m
end

function A.sameGuid(a, b)
    -- Osiris 有时返回 "名字_guid" 形式，统一取末尾 36 位比较
    if a == nil or b == nil then return false end
    return string.sub(a, -36) == string.sub(b, -36)
end

function A.getName(g)
    local handle = safe(Osi.GetDisplayName, g)
    if handle then
        local name = safe(Osi.ResolveTranslatedString, handle)
        if name and name ~= "" then return name end
    end
    return g
end

function A.getPos(g)
    local x, y, z = safe(Osi.GetPosition, g)
    if x == nil then return nil end
    return { A.round(x, 2), A.round(y, 2), A.round(z, 2) }
end

function A.isPartyMember(g)
    return safe(Osi.IsPartyMember, g, 1) == 1
end

-- 读调度器写的名单：{"rules": [["名字", 是否人类], ...], "default_human": false}，第一个匹配的生效
function A.isAIControlled(char)
    local raw = safe(Ext.IO.LoadFile, A.ROSTER_FILE)
    if not raw or raw == "" then return true end
    local ok, r = pcall(Ext.Json.Parse, raw)
    if not ok or type(r) ~= "table" then return true end
    local name = string.lower(A.getName(char) or "")
    for _, rule in ipairs(r.rules or {}) do
        local key = rule[1]
        if type(key) == "string" and key ~= "" and string.find(name, key, 1, true) then
            return not rule[2]
        end
    end
    return not r.default_human
end

----------------------------------------------------------------------
-- 资源记账
----------------------------------------------------------------------
-- 资源名 -> GUID（用于从角色身上扣除），优先查游戏静态数据，失败时用常见值
A.RESOURCE_FALLBACK = {
    ActionPoint = "734cbcfb-8922-4b6d-8330-b2a7e4c14b6a",
    BonusActionPoint = "420c8df5-45c2-4253-93c2-7ec44e127930",
    Movement = "d6b2369d-84f0-4ca4-a3a7-62d2d192a185",
    SpellSlot = "d136c5d9-0ff0-43da-acce-a74a07f8d6bf",
}
A.resourceIds = A.resourceIds or nil

function A.resourceUuid(name)
    if A.resourceIds == nil then
        A.resourceIds = {}
        local all = safe(Ext.StaticData.GetAll, "ActionResource") or {}
        for _, id in ipairs(all) do
            local res = safe(Ext.StaticData.Get, id, "ActionResource")
            local n = res and safe(function() return res.Name end)
            if n then A.resourceIds[n] = id end
        end
    end
    return A.resourceIds[name] or A.RESOURCE_FALLBACK[name]
end

function A.readResource(actor, name, level)
    return safe(Osi.GetActionResourceValuePersonal, actor, name, level or 0)
end

-- 解析 UseCosts，返回 { {name=, level=, amount=}, ... }；无法解析的（比如按距离计算的）忽略
function A.parseCosts(str)
    local out = {}
    if type(str) ~= "string" then return out end
    for part in string.gmatch(str, "[^;]+") do
        local fields = {}
        for f in string.gmatch(part, "[^:]+") do table.insert(fields, (f:gsub("^%s+", ""):gsub("%s+$", ""))) end
        local name = fields[1]
        if name == "SpellSlotsGroup" then
            -- SpellSlotsGroup:<组>:<数量>:<等级>
            table.insert(out, { name = "SpellSlot", level = tonumber(fields[4]) or 1, amount = tonumber(fields[3]) or 1 })
        elseif name and name ~= "" then
            local amount = tonumber(fields[2])
            if amount and amount > 0 then
                table.insert(out, { name = name, level = tonumber(fields[3]) or 0, amount = amount })
            end
        end
    end
    return out
end

function A.spellCostString(spellId)
    local stat = safe(Ext.Stats.Get, spellId)
    return stat and safe(function() return stat.UseCosts end) or ""
end

-- 当前可用量 = min(游戏里读到的值, 回合开始值 - 本回合已花)
function A.available(actor, name, level)
    local key = name .. ":" .. tostring(level or 0)
    local current = A.readResource(actor, name, level) or 0
    local entry = A.rt.ledger[key]
    if entry == nil then
        entry = { start = current, spent = 0, lastCur = current }
        A.rt.ledger[key] = entry
    end
    -- 游戏里的值涨了（疾跑把移动力翻倍、动作如潮多给一个动作……）：账本也跟着加
    if entry.lastCur and current > entry.lastCur + 0.01 then
        entry.start = entry.start + (current - entry.lastCur)
    end
    entry.lastCur = current
    return math.max(0, math.min(current, entry.start - entry.spent))
end

-- 从角色身上扣除（让界面同步）；失败也没关系，账本会兜底
function A.deductFromEntity(actor, name, level, amount)
    local uuid = A.resourceUuid(name)
    if not uuid then return false end
    local ok = pcall(function()
        local ent = Ext.Entity.Get(actor)
        local list = ent.ActionResources.Resources[uuid]
        for _, r in pairs(list) do
            if level == 0 or r.Level == level then
                r.Amount = math.max(0, r.Amount - amount)
                ent:Replicate("ActionResources")
                return
            end
        end
        error("resource entry not found")
    end)
    return ok
end

-- 只记账。真正从角色身上扣除放到施法完成之后（太早扣会让游戏以为没行动点而取消施法）；
-- 移动力由游戏在走路时自己扣，不需要我们扣。
function A.spend(actor, name, level, amount)
    local key = name .. ":" .. tostring(level or 0)
    A.available(actor, name, level) -- 确保账本里有这一项
    A.rt.ledger[key].spent = A.rt.ledger[key].spent + amount
end

function A.applyDeferred(actor, spell)
    for i = #A.rt.deferred, 1, -1 do
        local d = A.rt.deferred[i]
        if A.sameGuid(d.actor, actor) and (spell == nil or d.spell == spell) then
            table.remove(A.rt.deferred, i)
            for _, c in ipairs(d.costs) do
                if c.name ~= "Movement" then A.log("  扣除", c.name, c.level or 0, c.amount, "（" .. tostring(d.spell) .. "）") end
                if c.name ~= "Movement" and not A.deductFromEntity(d.actor, c.name, c.level or 0, c.amount) then
                    A.log("扣除资源失败（只记在账本里）:", c.name, c.level, c.amount)
                end
            end
            if spell ~= nil then return end
        end
    end
end

function A.canAfford(actor, costs)
    if not CONFIG.enforce_costs then return true end
    for _, c in ipairs(costs) do
        if A.available(actor, c.name, c.level) < c.amount then
            return false, c.name .. (c.level > 0 and (" L" .. c.level) or "")
        end
    end
    return true
end

----------------------------------------------------------------------
-- 状态导出
----------------------------------------------------------------------
-- 收集和 actor 同一场战斗里的单位（来源：Osiris 数据库 DB_Is_InCombat(角色, 战斗ID)）
function A.collectCombatants(actor)
    local list, seen = {}, {}
    local combat = safe(Osi.CombatGetGuidFor, actor)
    local rows = safe(function() return Osi.DB_Is_InCombat:Get(nil, nil) end) or {}
    for _, row in ipairs(rows) do
        local g, cg = row[1], row[2]
        local key = g and string.sub(g, -36)
        if g and not seen[key] and (combat == nil or A.sameGuid(cg, combat)) then
            seen[key] = true
            local d = safe(Osi.GetDistanceTo, actor, g)
            if d == nil or d <= CONFIG.max_unit_distance then
                table.insert(list, g)
            end
        end
    end
    return list
end

-- 读取角色的法术/动作列表（包括普通攻击这类“法术”）
function A.getAllSpells(g)
    local out, seen = {}, {}
    local ok, err = pcall(function()
        local ent = Ext.Entity.Get(g)
        local book = ent and ent.SpellBook
        if not (book and book.Spells) then return end
        for _, s in ipairs(book.Spells) do
            local id = s.Id and s.Id.Prototype
            if id and id ~= "" and not seen[id] then
                seen[id] = true
                table.insert(out, id)
            end
        end
    end)
    if not ok then A.log("读取法术列表失败:", err) end
    return out
end

-- 只返回现在付得起的技能，以及每个技能的消耗说明
-- 技能射程（米）：TargetRadius 是数字就用数字；近战武器约 1.5；远程武器约 18；读不到返回 nil
function A.spellRange(id)
    local stat = safe(Ext.Stats.Get, id)
    local tr = stat and safe(function() return stat.TargetRadius end)
    if tr == nil then return nil end
    local n = tonumber(tr)
    if n then return n end
    tr = tostring(tr)
    if tr:find("Melee") then return 1.5 end
    if tr:find("Ranged") then return 18 end
    return nil
end

function A.getUsableSpells(actor)
    local usable, costs = {}, {}
    for _, id in ipairs(A.getAllSpells(actor)) do
        if not (CONFIG.hidden_spells and CONFIG.hidden_spells[id]) then
            local costStr = A.spellCostString(id)
            if A.canAfford(actor, A.parseCosts(costStr)) then
                table.insert(usable, id)
                costs[id] = costStr
            end
        end
    end
    return usable, costs
end

-- 附近可以互动的物体（有“使用”组件、不在背包里、有名字）。实体列表 30 秒刷新一次
function A.itemEntities()
    local rt = A.rt
    if rt.itemCache and A.now() - rt.itemCache.at < 30000 then return rt.itemCache.list end
    local list = {}
    local all = safe(Ext.Entity.GetAllEntitiesWithComponent, "ServerItem") or {}
    -- 不能只看“使用”组件：魔舵（传送器）这类剧情物体没有它。全收下，背包里的和装饰品在后面滤掉
    for _, e in ipairs(all) do
        local guid = safe(function() return e.Uuid.EntityUuid end)
        if guid then list[#list + 1] = guid end
    end
    rt.itemCache = { at = A.now(), list = list }
    A.log("可互动物体缓存：" .. #list .. " 个（共 " .. #all .. " 个物品）")
    return list
end

-- 返回 radius 以内的物体（最近的若干个），和 landmark_radius 以内每种名字最近的一个（给调度器按剧情挑目标）
function A.nearbyObjects(actor, radius, wantLandmarks)
    local near, byName = {}, {}
    for _, g in ipairs(A.itemEntities()) do
        local d = safe(Osi.GetDistanceTo, actor, g)
        if d and d <= math.max(radius, CONFIG.landmark_radius) and safe(Osi.GetInventoryOwner, g) == nil then
            local name = A.getName(g)
            if name and name ~= g and not name:find("%x+%-%x+%-%x+") then
                local o = { guid = g, name = name, dist = A.round(d, 1) }
                if d <= radius then near[#near + 1] = o end
                if not byName[name] or byName[name].dist > o.dist then byName[name] = o end
            end
        end
    end
    table.sort(near, function(a, b) return a.dist < b.dist end)
    while #near > CONFIG.max_objects do table.remove(near) end
    for _, o in ipairs(near) do o.pos = A.getPos(o.guid) end
    if not wantLandmarks then return near end
    local marks = {}
    for _, o in pairs(byName) do marks[#marks + 1] = o end
    table.sort(marks, function(a, b) return a.dist < b.dist end)
    while #marks > 120 do table.remove(marks) end
    for _, o in ipairs(marks) do o.pos = A.getPos(o.guid) end
    return near, marks
end

-- 使用物体：试几种 Osiris 调用，哪个能用用哪个
function A.useObject(actor, cmd)
    local item = cmd.target
    for _, fn in ipairs({ "Use", "CharacterUseItem", "UseItem" }) do
        local ok, err = pcall(function() return Osi[fn](actor, item, "") end)
        A.log(A.getName(actor), "使用", A.getName(item), "（Osi." .. fn .. "）", ok and "" or tostring(err))
        if ok then
            local p = A.getPos(item)
            A.waitFor(actor, "move", nil, p)
            if A.rt.waiting then A.rt.waiting.near = 2.0 end   -- 走到物体旁边就算完成
            return "used " .. A.getName(item)
        end
    end
    -- 都不行：至少走过去
    return A.move(actor, { position = cmd.position or A.getPos(item) })
end

function A.exportTurn(actor)
    local rt = A.rt
    rt.seq = rt.seq + 1
    local units = {}
    for _, g in ipairs(A.collectCombatants(actor)) do
        if not A.sameGuid(g, actor) then
            table.insert(units, {
                guid = g,
                name = A.getName(g),
                hp = safe(Osi.GetHitpoints, g),
                max_hp = safe(Osi.GetMaxHitpoints, g),
                enemy = safe(Osi.IsEnemy, actor, g) == 1,
                ally = A.isPartyMember(g),
                dead = safe(Osi.IsDead, g) == 1,
                pos = A.getPos(g),
                dist = A.round(safe(Osi.GetDistanceTo, actor, g), 1),
            })
        end
    end

    local spells, spellCosts = A.getUsableSpells(actor)
    local state = {
        session = A.SESSION,
        seq = rt.seq,
        type = "turn",
        combat = safe(Osi.CombatGetGuidFor, actor),
        actions_taken = rt.actionsThisTurn,
        last_result = rt.lastResult,
        actor = {
            guid = actor,
            name = A.getName(actor),
            hp = safe(Osi.GetHitpoints, actor),
            max_hp = safe(Osi.GetMaxHitpoints, actor),
            pos = A.getPos(actor),
            resources = {
                action = A.available(actor, "ActionPoint", 0),
                bonus_action = A.available(actor, "BonusActionPoint", 0),
                movement = A.round(A.available(actor, "Movement", 0), 1),
                spell_slots = {
                    A.available(actor, "SpellSlot", 1),
                    A.available(actor, "SpellSlot", 2),
                    A.available(actor, "SpellSlot", 3),
                },
            },
            spells = spells,
            spell_costs = spellCosts,
            spell_ranges = (function()
                local r = {}
                for _, id in ipairs(spells) do r[id] = A.spellRange(id) end
                return r
            end)(),
        },
        units = units,
        objects = {},
        landmarks = {},
    }

    local okO, near, marks = pcall(A.nearbyObjects, actor, CONFIG.object_radius, true)
    if okO then
        state.objects, state.landmarks = near or {}, marks or {}
    else
        A.log("读取附近物体失败:", near)
    end

    local ok, err = pcall(function()
        Ext.IO.SaveFile(A.STATE_FILE, Ext.Json.Stringify(state))
    end)
    if not ok then
        A.log("写入状态文件失败:", err)
        return
    end
    rt.pending = { seq = rt.seq, actor = actor }
    A.log(string.format("已导出 #%d：%s 的回合（第 %d 个动作，行动 %s / 附赠 %s / 移动 %s）",
        rt.seq, state.actor.name, rt.actionsThisTurn + 1,
        tostring(state.actor.resources.action), tostring(state.actor.resources.bonus_action),
        tostring(state.actor.resources.movement)))
end

----------------------------------------------------------------------
-- 执行
----------------------------------------------------------------------
function A.endTurn(actor)
    A.log("结束回合:", A.getName(actor))
    A.rt.pending = nil
    A.rt.currentActor = nil
    A.rt.waiting = nil
    safe(Osi.EndTurn, actor)
    -- 有时游戏没理会（角色还在收尾动作）：2.5 秒后还没收到 TurnEnded 就再结束一次，最多 3 次
    A.rt.endPending = { actor = actor, tries = 1 }
    local function check()
        local ep = A.rt.endPending
        if not (ep and A.sameGuid(ep.actor, actor)) then return end
        if ep.tries >= 3 then A.rt.endPending = nil return end
        ep.tries = ep.tries + 1
        A.log("回合好像没结束，再试一次:", A.getName(actor))
        safe(Osi.EndTurn, actor)
        A.schedule(2500, check)
    end
    A.schedule(2500, check)
    A.startNextQueued()
end

-- 共享回合：上一个 AI 角色打完后，接着让排队的下一个 AI 角色行动
function A.startNextQueued()
    local rt = A.rt
    A.schedule(800, function()
        if rt.currentActor then return end
        while #rt.turnQueue > 0 do
            local nextChar = table.remove(rt.turnQueue, 1)
            if safe(Osi.IsDead, nextChar) ~= 1 then
                A.log("轮到排队的角色:", A.getName(nextChar))
                A.beginTurn(nextChar)
                return
            end
        end
    end)
end

function A.useSpell(actor, cmd)
    local costs = A.parseCosts(A.spellCostString(cmd.spell))
    local okAfford, missing = A.canAfford(actor, costs)
    if not okAfford then
        A.log(A.getName(actor), "资源不足，拒绝:", cmd.spell, missing)
        return "rejected: not enough " .. tostring(missing) .. " for " .. cmd.spell
    end
    local ok, err = pcall(Osi.UseSpell, actor, cmd.spell, cmd.target)
    A.log(A.getName(actor), "->", cmd.spell, "->", A.getName(cmd.target), ok and "" or tostring(err))
    if not ok then
        return "failed: " .. tostring(err)
    end
    if CONFIG.enforce_costs then
        for _, c in ipairs(costs) do A.spend(actor, c.name, c.level, c.amount) end
        table.insert(A.rt.deferred, { actor = actor, spell = cmd.spell, costs = costs })
    end
    A.waitFor(actor, "spell", cmd.spell)
    return "used " .. cmd.spell
end

-- 找一个真的走得到的落点：GetHeightsAt 给出 (x, z) 处每一层地面的高度（多层楼时有好几个），
-- 从离角色最近的那层开始，用游戏自己的寻路验证；落点被挪开超过 1.5 米就算走不到。
-- （参考 Move With Minimap 模组的做法。CharacterMoveToPosition 遇到走不到的点会直接瞬移过去，所以必须先验证）
function A.reachableY(actor, actorY, x, z)
    if not (Ext.Level and Ext.Level.GetHeightsAt and Ext.Level.BeginPathfindingImmediate) then
        return actorY    -- 这个版本的 Script Extender 没有寻路接口：照旧直接走
    end
    local ent = safe(Ext.Entity.Get, actor)
    local heights = ent and safe(Ext.Level.GetHeightsAt, x, z)
    if not heights or #heights == 0 then return nil, "no ground" end
    local ordered = {}
    for k, h in ipairs(heights) do ordered[k] = h end
    table.sort(ordered, function(a, b) return math.abs(a - actorY) < math.abs(b - actorY) end)
    for _, y in ipairs(ordered) do
        if math.abs(y - actorY) < 6 then        -- 不跨楼层（差太多就是别的楼层了）
            local okP, path = pcall(Ext.Level.BeginPathfindingImmediate, ent, { x, y, z })
            if okP and path then
                local found, snap = false, nil
                pcall(function()
                    found = Ext.Level.FindPath(path)
                    if found then
                        local ta = path.TargetAdjusted
                        local tx, tz = ta[1] or ta.x, ta[3] or ta.z
                        if tx and tz then snap = math.sqrt((tx - x) ^ 2 + (tz - z) ^ 2) end
                    end
                end)
                pcall(Ext.Level.ReleasePath, path)
                if found and snap and snap <= 1.5 then return y end
            end
        end
    end
    return nil, "no path"
end

function A.move(actor, cmd)
    local p = cmd.position
    local from = A.getPos(actor)
    if not from then return "move failed: unknown position" end
    local dx, dz = p[1] - from[1], p[3] - from[3]
    local dist = math.sqrt(dx * dx + dz * dz)
    local budget = CONFIG.enforce_costs and A.available(actor, "Movement", 0) or dist
    if budget <= 0.3 then
        return "rejected: no movement left"
    end
    local reach = math.min(dist, budget)
    -- 从最远往回试：能走到的最远一点
    local dest
    for _, f in ipairs({ 1.0, 0.8, 0.6, 0.4, 0.25 }) do
        local d = reach * f
        if d < 0.5 then break end
        local x, z = from[1] + dx / dist * d, from[3] + dz / dist * d
        local y = A.reachableY(actor, from[2], x, z)
        if y then dest = { x, y, z, d } break end
    end
    if not dest then
        A.log(A.getName(actor), "找不到能走过去的路")
        return "rejected: no walkable path in that direction (blocked, or it is on another floor). Try another target."
    end
    if CONFIG.enforce_costs then A.spend(actor, "Movement", 0, dest[4]) end
    -- 最后一个参数 0：走不到也不要瞬移
    local ok, err = pcall(Osi.CharacterMoveToPosition, actor, dest[1], dest[2], dest[3], "Run", "AIPlays_MoveDone", 0)
    A.log(A.getName(actor), "移动到", A.round(dest[1], 1), A.round(dest[2], 1), A.round(dest[3], 1),
        string.format("（%.1f 米）", dest[4]), ok and "" or tostring(err))
    if ok then
        A.waitFor(actor, "move", nil, { dest[1], dest[2], dest[3] })
        A.rt.waiting.from, A.rt.waiting.planned = from, dest[4]
    end
    return ok and string.format("moved %.1fm", dest[4]) or ("move failed: " .. tostring(err))
end

function A.execute(actor, cmd)
    local rt = A.rt
    rt.actionsThisTurn = rt.actionsThisTurn + 1
    local action = cmd.action

    if action == "use_spell" and cmd.spell and cmd.target then
        rt.lastResult = A.useSpell(actor, cmd)
    elseif action == "move" and type(cmd.position) == "table" then
        rt.lastResult = A.move(actor, cmd)
    elseif action == "use_object" and cmd.target then
        rt.lastResult = A.useObject(actor, cmd)
    else
        rt.lastResult = nil
        A.endTurn(actor)
        return
    end

    -- 被拒绝/失败的动作没有动画，马上继续；成功的动作等“完成”事件（或超时）再继续
    if rt.waiting == nil then
        A.continueTurn(actor, 200)
    end
end

-- 开始等待一个动作完成；超时也会继续，防止事件没来导致卡住
function A.waitFor(actor, kind, spell, dest)
    local rt = A.rt
    rt.token = rt.token + 1
    local token = rt.token
    local t = A.now()
    rt.waiting = { actor = actor, kind = kind, spell = spell, token = token,
                   dest = dest, startedAt = t, lastPos = A.getPos(actor), lastMoveAt = t }
    A.schedule(CONFIG.action_timeout_ms, function()
        if rt.waiting and rt.waiting.token == token then
            A.log("等待动作完成超时，继续")
            A.actionFinished(actor, true)
        end
    end)
end

-- 技能等到超时都没有“施法完成”事件：说明游戏没放出来（距离、视线、目标不合法……）。
-- 把账本里记的消耗退回去，并告诉 AI 换个做法。
function A.refund(actor, spell)
    for i = #A.rt.deferred, 1, -1 do
        local d = A.rt.deferred[i]
        if A.sameGuid(d.actor, actor) and d.spell == spell then
            table.remove(A.rt.deferred, i)
            for _, c in ipairs(d.costs) do
                local entry = A.rt.ledger[c.name .. ":" .. tostring(c.level or 0)]
                if entry then entry.spent = math.max(0, entry.spent - c.amount) end
            end
            return true
        end
    end
end

function A.actionFinished(actor, timedOut)
    local rt = A.rt
    if not (rt.waiting and A.sameGuid(rt.waiting.actor, actor)) then return end
    local w = rt.waiting
    rt.waiting = nil
    if w.kind == "spell" and timedOut and not w.started then
        A.refund(actor, w.spell)
        rt.lastResult = "failed: the game did not perform " .. tostring(w.spell)
            .. " (target out of range / not visible / invalid). No resources were spent. Try something else."
        A.log(A.getName(actor), w.spell, "没有放出来，已退还资源")
        A.continueTurn(actor, 200)
        return
    end
    if w.kind == "spell" then A.applyDeferred(actor, w.spell) end
    -- 移动被打断（借机攻击、被挡住……）：没走完的距离退回账本，告诉 AI 还能接着走
    if w.kind == "move" and w.from and w.planned then
        local now = A.getPos(actor)
        if now then
            local actual = math.sqrt((now[1] - w.from[1]) ^ 2 + (now[3] - w.from[3]) ^ 2)
            if actual < w.planned - 1.0 then
                local entry = rt.ledger["Movement:0"]
                if entry then entry.spent = math.max(0, entry.spent - (w.planned - actual)) end
                rt.lastResult = string.format("moved only %.1fm of %.1fm — the move was interrupted (an opportunity "
                    .. "attack, or something in the way). Your unused movement is still available: you can move again.",
                    actual, w.planned)
                A.log(A.getName(actor), string.format("移动被打断：只走了 %.1f / %.1f 米，已退还", actual, w.planned))
            end
        end
    end
    A.continueTurn(actor, CONFIG.settle_ms)
end

function A.continueTurn(actor, delay)
    local rt = A.rt
    if rt.actionsThisTurn >= CONFIG.max_actions_per_turn then
        A.log("达到单回合动作上限")
        A.schedule(delay, function()
            if rt.currentActor and A.sameGuid(rt.currentActor, actor) then A.endTurn(actor) end
        end)
        return
    end
    A.schedule(delay, function()
        if rt.currentActor and A.sameGuid(rt.currentActor, actor) then A.exportTurn(actor) end
    end)
end

-- 技能开始施放（UsingSpell）：说明游戏接受了，超时也不退资源
function A.onUsingSpell(caster, spell)
    local w = A.rt.waiting
    if A.isPartyMember(caster) then A.log("UsingSpell", A.getName(caster), spell) end
    if w and w.kind == "spell" and A.sameGuid(w.actor, caster) then w.started = true end
end

function A.onCastedSpell(caster, spell)
    local w = A.rt.waiting
    if A.isPartyMember(caster) then A.log("CastedSpell", A.getName(caster), spell) end
    -- 一次只等一个动作，所以这个角色的任何“施法完成”都算（技能名有时和我们发的不完全一样）
    if w and w.kind == "spell" and A.sameGuid(w.actor, caster) then
        if w.spell ~= spell then A.log("  （施法完成的技能名和指令不同：", w.spell, "vs", spell, "）") end
        A.actionFinished(caster)
    end
end

-- CharacterMoveToPosition 走完后，BG3 会发 EntityEvent(角色, "AIPlays_MoveDone")
function A.onEntityEvent(obj, event)
    local w = A.rt.waiting
    if event == "AIPlays_MoveDone" and w and w.kind == "move" and A.sameGuid(w.actor, obj) then
        A.actionFinished(obj)
    end
end

-- 保险：直接看位置。到了目的地，或者已经停下来不动了，就当移动完成
-- 技能等太久：既没开始施放（UsingSpell），角色也没走动 —— 游戏没接受，早点判失败，别干等 9 秒
function A.checkSpellProgress()
    local w = A.rt.waiting
    if not (w and w.kind == "spell") or w.started then return end
    local pos = A.getPos(w.actor)
    if pos and w.lastPos and (math.abs(pos[1] - w.lastPos[1]) + math.abs(pos[3] - w.lastPos[3])) > 0.1 then
        w.moved = true   -- 在走过去，等它
    end
    if not w.moved and A.now() - w.startedAt > CONFIG.fail_fast_ms then
        A.actionFinished(w.actor, true)
    end
end

function A.checkMoveProgress()
    local w = A.rt.waiting
    if not (w and w.kind == "move") then return end
    local pos = A.getPos(w.actor)
    if not pos then return end
    local t = A.now()
    local function d2(a, b) return math.sqrt((a[1] - b[1]) ^ 2 + (a[3] - b[3]) ^ 2) end
    if w.dest and d2(pos, w.dest) < (w.near or 0.8) then
        A.actionFinished(w.actor)
        return
    end
    if w.lastPos and d2(pos, w.lastPos) > 0.05 then
        w.lastPos, w.lastMoveAt = pos, t
    elseif t - w.startedAt > 1000 and t - w.lastMoveAt > 1200 then
        A.actionFinished(w.actor)
    end
end

----------------------------------------------------------------------
-- 事件（监听函数只注册一次，内部调用 A.xxx，所以补丁覆盖后立即生效）
----------------------------------------------------------------------
-- 调度器心跳：调度器每秒写一次 AIPlays_director.json（内容会变）；暂停时里面带 "paused": true
function A.directorState()
    local raw = safe(Ext.IO.LoadFile, "AIPlays_director.json")
    local t = A.now()
    if raw ~= A.rt.hbRaw then A.rt.hbRaw = raw; A.rt.hbAt = t end
    if raw == nil or raw == "" then return false, "调度器没开" end
    if A.rt.hbAt == nil or t - A.rt.hbAt > CONFIG.heartbeat_ms then return false, "调度器没开" end
    if raw:find('"paused"%s*:%s*true') then return false, "AI 已暂停" end
    return true
end

function A.onTurnStarted(char)
    if not CONFIG.enabled then return end
    if not A.isPartyMember(char) then return end
    if CONFIG.require_director then
        local ok, why = A.directorState()
        if not ok then
            A.log(A.getName(char), why .. "，这个回合由玩家操作")
            return
        end
    end
    if not A.isAIControlled(char) then
        A.log(A.getName(char), "归玩家操作，AI 不接管")
        return
    end
    local rt = A.rt
    if rt.currentActor then
        -- 共享回合：已经有 AI 角色在行动，先排队
        if A.sameGuid(rt.currentActor, char) then return end
        for _, q in ipairs(rt.turnQueue) do
            if A.sameGuid(q, char) then return end
        end
        table.insert(rt.turnQueue, char)
        A.log(A.getName(char), "排队等待（共享回合）")
        return
    end
    A.beginTurn(char)
end

function A.beginTurn(char)
    local rt = A.rt
    rt.currentActor = char
    rt.actionsThisTurn = 0
    rt.lastResult = nil
    rt.ledger = {}
    rt.waiting = nil
    rt.deferred = {}
    A.schedule(CONFIG.turn_start_delay_ms, function()
        if rt.currentActor and A.sameGuid(rt.currentActor, char) then A.exportTurn(char) end
    end)
end

function A.onTurnEnded(char)
    if A.rt.endPending and A.sameGuid(A.rt.endPending.actor, char) then A.rt.endPending = nil end
    A.applyDeferred(char, nil)   -- 施法完成事件晚到的，回合结束时补扣
    local rt = A.rt
    for i = #rt.turnQueue, 1, -1 do
        if A.sameGuid(rt.turnQueue[i], char) then table.remove(rt.turnQueue, i) end
    end
    if rt.currentActor and A.sameGuid(rt.currentActor, char) then
        -- 不是 AI 自己结束的（比如你手动结束了这个角色的回合），也要轮到下一个
        rt.currentActor = nil
        rt.pending = nil
        rt.waiting = nil
        A.startNextQueued()
    end
end

function A.onCombatEnded()
    A.rt.currentActor = nil
    A.rt.pending = nil
    A.rt.waiting = nil
    A.rt.turnQueue = {}
    A.rt.scheduled = {}
end

-- 队伍成员名单（给调度器的剧情模块用：只让在队伍里的角色参加“队伍议会”）
-- 角色卡（升级用）：等级、可升到的等级、职业、属性。读不到的字段就留空，不影响别的
local ABILITIES = { "Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma" }
local function className(uuid)
    if not uuid or uuid == "00000000-0000-0000-0000-000000000000" then return nil end
    local cd = safe(Ext.StaticData.Get, uuid, "ClassDescription")
    if not cd then return nil end
    local disp = safe(function() return cd.DisplayName:Get() end)
    return (disp and disp ~= "") and disp or safe(function() return cd.Name end)
end

function A.characterSheet(g)
    local sheet = {}
    local ent = safe(Ext.Entity.Get, g)
    if not ent then return sheet end
    sheet.level = safe(function() return ent.EocLevel.Level end)
    sheet.available_level = safe(function() return ent.AvailableLevel.Level end)
    sheet.xp = safe(function() return ent.Experience.TotalExperience end)
    sheet.next_xp = safe(function() return ent.Experience.NextLevelExperience end)
    local classes = {}
    safe(function()
        for _, c in ipairs(ent.Classes.Classes) do
            classes[#classes + 1] = { class = className(c.ClassUUID), subclass = className(c.SubClassUUID), level = c.Level }
        end
    end)
    sheet.classes = classes
    local ab = {}
    safe(function()
        local arr = ent.Stats.Abilities
        for k, name in ipairs(ABILITIES) do ab[name] = arr[k + 1] end   -- 第 1 个是 None
    end)
    sheet.abilities = ab
    return sheet
end

-- 队伍名单 + 角色卡（给调度器：剧情议会只让在队伍里的人发言；升级时让 AI 做决定）
function A.exportParty()
    local names, members = {}, {}
    local rows = Osi.DB_Players:Get(nil) or {}
    for _, row in ipairs(rows) do
        local n = A.getName(row[1])
        if n and n ~= "" then
            names[#names + 1] = n
            local m = A.characterSheet(row[1])
            m.name, m.guid = n, row[1]
            members[#members + 1] = m
        end
    end
    table.sort(names)
    table.sort(members, function(a, b) return a.name < b.name end)
    local body = Ext.Json.Stringify({ names = names, members = members })
    if body ~= A.rt.partyJson then
        A.rt.partyJson = body
        Ext.IO.SaveFile("AIPlays_party.json", body)
    end
end

function A.onTick()
    local rt = A.rt
    rt.tickCount = rt.tickCount + 1

    if #rt.scheduled > 0 then
        local t = A.now()
        for i = #rt.scheduled, 1, -1 do
            local task = rt.scheduled[i]
            if t >= task.at then
                table.remove(rt.scheduled, i)
                local ok, err = pcall(task.fn)
                if not ok then A.log("延迟任务出错:", err) end
            end
        end
    end

    if rt.waiting and rt.tickCount % 6 == 0 then
        A.checkMoveProgress()
        A.checkSpellProgress()
    end
    if rt.tickCount % 90 == 0 then safe(A.exportParty) end
    if CONFIG.require_director and rt.tickCount % 30 == 0 then
        local ok, why = A.directorState()
        if not ok and rt.currentActor then
            A.log(why .. "：把 " .. A.getName(rt.currentActor) .. " 交还给玩家操作")
            rt.currentActor, rt.pending, rt.waiting = nil, nil, nil
            rt.turnQueue = {}
        end
    end

    if rt.pending == nil or rt.tickCount % CONFIG.poll_every_ticks ~= 0 then return end

    local raw = safe(Ext.IO.LoadFile, A.CMD_FILE)
    if raw == nil or raw == "" then return end
    local ok, cmd = pcall(Ext.Json.Parse, raw)
    if not ok or type(cmd) ~= "table" then return end
    if cmd.session ~= A.SESSION or cmd.seq ~= rt.pending.seq then return end

    local actor = rt.pending.actor
    rt.pending = nil
    local okExec, err = pcall(A.execute, actor, cmd)
    if not okExec then
        A.log("执行指令出错:", err)
        A.endTurn(actor)
    end
end

function A.reload()
    local code = safe(Ext.IO.LoadFile, A.PATCH_FILE)
    if not code then
        A.log("没找到 " .. A.PATCH_FILE)
        return
    end
    -- Script Extender 的 load 是 load(代码, 环境表)；标准 Lua 是 load(代码, 名字, 模式, 环境表)，两种都试
    local chunk, err
    local okSE, r1, r2 = pcall(load, code, _ENV)
    if okSE and r1 then
        chunk = r1
    else
        local okStd, s1, s2 = pcall(load, code, "=AIPlays_patch", "t", _ENV)
        chunk, err = okStd and s1 or nil, okStd and s2 or s1
        if not chunk and okSE then err = r2 end
    end
    if not chunk then
        A.log("补丁语法错误:", err)
        return
    end
    local ok, runErr = pcall(chunk)
    A.log(ok and "补丁已加载" or ("补丁运行出错: " .. tostring(runErr)))
end

function A.console(arg)
    A.forcePrint = true
    local ok, err = pcall(A.consoleInner, arg)
    A.forcePrint = false
    if not ok then Ext.Utils.Print("[AIPlays] 命令出错:", err) end
end

function A.consoleInner(arg)
    local rt = A.rt
    if arg == "on" then
        CONFIG.enabled = true
        A.log("AI 控制已开启")
    elseif arg == "off" then
        CONFIG.enabled = false
        rt.pending = nil
        rt.currentActor = nil
        A.log("AI 控制已关闭")
    elseif arg == "reload" then
        A.reload()
    elseif arg == "verbose" then
        CONFIG.verbose = not CONFIG.verbose
        A.log("控制台日志：", CONFIG.verbose and "打开" or "关闭（只写 AIPlays_log.txt）")
    elseif arg == "dumpitems" then
        -- 排查用：把所有物品（不管有没有“使用”组件）的名字、距离、是否在背包里都写出来
        local host = Osi.GetHostCharacter()
        local hp = A.getPos(host) or { 0, 0, 0 }
        local lines = {}
        for _, e in ipairs(safe(Ext.Entity.GetAllEntitiesWithComponent, "ServerItem") or {}) do
            local g = safe(function() return e.Uuid.EntityUuid end) or "?"
            local name = A.getName(g)
            local p = A.getPos(g)
            local d = p and math.sqrt((p[1] - hp[1]) ^ 2 + (p[3] - hp[3]) ^ 2) or -1
            local hasUse = safe(function() return e.Use ~= nil end) and "use" or "-"
            local owner = safe(Osi.GetInventoryOwner, g)
            lines[#lines + 1] = string.format("%.1f\t%s\t%s\t%s\t%s", d, hasUse, owner and "inv" or "-", tostring(name), g)
        end
        table.sort(lines)
        Ext.IO.SaveFile("AIPlays_items_all.txt", table.concat(lines, "\n"))
        A.log("已保存 AIPlays_items_all.txt：" .. #lines .. " 个物品")
    elseif arg == "allitems" then
        local host = Osi.GetHostCharacter()
        A.rt.itemCache = nil
        local _, marks = A.nearbyObjects(host, CONFIG.object_radius, true)
        local lines = {}
        for _, o in ipairs(marks or {}) do lines[#lines + 1] = string.format("%.1f\t%s\t%s", o.dist, o.name, o.guid) end
        Ext.IO.SaveFile("AIPlays_items.txt", table.concat(lines, "\n"))
        A.log("已保存 AIPlays_items.txt：" .. #lines .. " 种物体")
    elseif arg == "items" then
        local host = Osi.GetHostCharacter()
        A.rt.itemCache = nil
        for i, o in ipairs(A.nearbyObjects(host, CONFIG.object_radius)) do
            A.log(string.format("  O%d  %s  %.1fm  %s", i, o.name, o.dist, o.guid))
        end
    else
        A.log("enabled =", tostring(CONFIG.enabled), "enforce_costs =", tostring(CONFIG.enforce_costs),
            "session =", A.SESSION, "seq =", rt.seq, "pending =", rt.pending and rt.pending.seq or "none")
    end
end

-- 监听只注册一次；热加载时只补注册新增的监听
A.hooks = A.hooks or {}
if A.registered and not A.hooks.TurnStarted then
    -- 旧版本已经注册过这几个
    for _, k in ipairs({ "TurnStarted", "TurnEnded", "CombatEnded", "Tick", "Console" }) do A.hooks[k] = true end
end
A.registered = true
local function hook(name, fn)
    if A.hooks[name] then return end
    A.hooks[name] = true
    local ok, err = pcall(fn)
    if not ok then A.log("注册监听失败:", name, err) end
end
hook("TurnStarted", function() Ext.Osiris.RegisterListener("TurnStarted", 1, "after", function(c) A.onTurnStarted(c) end) end)
hook("TurnEnded", function() Ext.Osiris.RegisterListener("TurnEnded", 1, "after", function(c) A.onTurnEnded(c) end) end)
hook("CombatEnded", function() Ext.Osiris.RegisterListener("CombatEnded", 1, "after", function(c) A.onCombatEnded(c) end) end)
hook("CastedSpell", function() Ext.Osiris.RegisterListener("CastedSpell", 5, "after", function(c, s) A.onCastedSpell(c, s) end) end)
hook("UsingSpell", function() Ext.Osiris.RegisterListener("UsingSpell", 5, "after", function(c, s) A.onUsingSpell(c, s) end) end)
hook("EntityEvent", function() Ext.Osiris.RegisterListener("EntityEvent", 2, "after", function(o, e) A.onEntityEvent(o, e) end) end)
hook("Tick", function() Ext.Events.Tick:Subscribe(function() A.onTick() end) end)
hook("Console", function() Ext.RegisterConsoleCommand("aiplays", function(_, arg) A.console(arg) end) end)

A.log("AIPlays 已加载，session =", A.SESSION)
Ext.Utils.Print("[AIPlays] 已加载（日志在 AIPlays_log.txt；!aiplays verbose 可在控制台显示）")

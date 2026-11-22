-- Pokemon-HGSS-MCP bridge (runs inside BizHawk's Lua console).
--
-- File-based IPC: each frame, if <ipc>/cmd.txt exists, read and delete it, run its ops, then write
-- <ipc>/resp.txt. Nothing here ever blocks, so the emulator keeps running normally when no
-- controller is attached (a socket read would freeze BizHawk while it waits).
--
-- cmd.txt:  first line "id <n>", then one op per line:
--   press <Btn,Btn> <frames>   hold buttons (BizHawk NDS names: A B X Y L R Start Select Up Down Left Right)
--   wait <frames>              advance with no input
--   touch <x> <y> <frames>     touch the bottom screen (0-255, 0-191)
--   shot <path>                screenshot at native resolution (both screens, 256x384)
--   read <domain> <addr> <len> <path>   dump memory bytes to a file (spaces in domain as _SP_)
--   speed <percent>            emulation speed (100 = normal)
--   flush                      write SaveRAM (the in-game save) to disk
--   savestate <path> / loadstate <path>   (development only; the MCP server never exposes these)
--   info                       frame count, memory domains, joypad button names
-- resp.txt: "id <n>", "frame <n>", then any "key value" result lines.

local src = debug.getinfo(1, "S").source:gsub("^@", "")
local dir = src:match("^(.*)[/\\]") or "."
-- The Python side passes its IPC folder in HGSS_IPC (see emu.launch); fallback: <repo>/work/ipc.
local IPC = os.getenv("HGSS_IPC") or (dir .. "/../work/ipc")
IPC = IPC:gsub("[/\\]$", "") .. "/"
local CMD, RESP, TMP = IPC .. "cmd.txt", IPC .. "resp.txt", IPC .. "resp.tmp"

local queue, cur, cmd_id, results = {}, nil, nil, {}

local function words(line)
  local t = {}
  for w in line:gmatch("%S+") do t[#t + 1] = w end
  return t
end

local function buttons(spec)
  local b = {}
  if spec and spec ~= "-" then
    for name in spec:gmatch("[^,]+") do b[name] = true end
  end
  return b
end

local function result(k, v) results[#results + 1] = k .. " " .. tostring(v) end

local function immediate(w)
  local op = w[1]
  if op == "shot" then
    client.screenshot(w[2])
    result("shot", w[2])
  elseif op == "read" then
    local addr = tonumber(w[3]) or tonumber(w[3], 16)
    local domain = w[2]:gsub("_SP_", " ")  -- domain names contain spaces ("Main RAM")
    local s = memory.read_bytes_as_binary_string(addr, tonumber(w[4]), domain)
    local f = io.open(w[5], "wb"); f:write(s); f:close()
    result("read", w[5] .. " " .. #s)
  elseif op == "flush" then
    client.saveram()  -- write the in-game save (SaveRAM) to disk now, not only on exit
    result("flush", "ok")
  elseif op == "speed" then
    client.speedmode(tonumber(w[2]))
    result("speed", w[2])
  elseif op == "savestate" then
    savestate.save(w[2]); result("savestate", w[2])
  elseif op == "loadstate" then
    savestate.load(w[2]); result("loadstate", w[2])
  elseif op == "joy" then
    local parts = {}
    for k, v in pairs(joypad.get()) do parts[#parts + 1] = k .. "=" .. tostring(v) end
    table.sort(parts)
    result("joy", table.concat(parts, "|"))
  elseif op == "info" then
    result("domains", table.concat(memory.getmemorydomainlist(), "|"))
    local names = {}
    for k, _ in pairs(joypad.get()) do names[#names + 1] = k end
    table.sort(names)
    result("buttons", table.concat(names, "|"))
    result("rom", gameinfo.getromname())
  else
    result("error", "unknown op " .. tostring(op))
  end
end

local function poll()
  local f = io.open(CMD, "r")
  if not f then return end
  local text = f:read("a")
  f:close()
  os.remove(CMD)
  results = {}
  for line in text:gmatch("[^\r\n]+") do
    local w = words(line)
    if w[1] == "id" then cmd_id = w[2]
    elseif w[1] == "press" then queue[#queue + 1] = { b = buttons(w[2]), n = tonumber(w[3]) or 1 }
    elseif w[1] == "wait" then queue[#queue + 1] = { b = {}, n = tonumber(w[2]) or 1 }
    elseif w[1] == "touch" then
      queue[#queue + 1] = { b = { Touch = true }, x = tonumber(w[2]), y = tonumber(w[3]), n = tonumber(w[4]) or 2 }
    elseif w[1] then
      -- paths may contain spaces: the path is always the rest of the line
      local fixed = ({ shot = 1, savestate = 1, loadstate = 1, read = 4 })[w[1]]
      if fixed then
        local rest = line
        for _ = 1, fixed do rest = rest:gsub("^%s*%S+%s", "", 1) end
        for i = #w, fixed + 1, -1 do w[i] = nil end
        w[fixed + 1] = rest
      end
      queue[#queue + 1] = { now = w }
    end
  end
  cmd_id = cmd_id or "0"
end

local function respond()
  local f = io.open(TMP, "w")
  f:write("id " .. cmd_id .. "\nframe " .. emu.framecount() .. "\n")
  for _, r in ipairs(results) do f:write(r .. "\n") end
  f:close()
  os.remove(RESP)
  os.rename(TMP, RESP)
  cmd_id = nil
end

-- Touch coordinates set before frameadvance don't reach the melonDS core; apply them when the
-- core polls input instead.
event.oninputpoll(function()
  if cur and cur.x then
    joypad.setanalog({ ["Touch X"] = cur.x, ["Touch Y"] = cur.y })
  end
end, "touch")

-- Every screenshot would otherwise flash "screenshot saved" over the game.
pcall(client.displaymessages, false)

console.log("Pokemon-HGSS-MCP bridge ready; IPC folder " .. IPC)
while true do
  if cmd_id == nil then poll() end
  while cur == nil and #queue > 0 do
    local op = table.remove(queue, 1)
    if op.now then
      local ok, err = pcall(immediate, op.now)
      if not ok then result("error", err) end
    else
      cur = op
    end
  end
  if cur then
    if cur.x then
      joypad.setanalog({ ["Touch X"] = cur.x, ["Touch Y"] = cur.y })
      joypad.setanalog({ ["Touch X"] = cur.x, ["Touch Y"] = cur.y }, 1)
    end
    joypad.set(cur.b)
    cur.n = cur.n - 1
    if cur.n <= 0 then cur = nil end
  end
  if cmd_id ~= nil and cur == nil and #queue == 0 then respond() end
  emu.frameadvance()
end

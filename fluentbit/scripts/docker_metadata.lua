local cjson_ok, cjson = pcall(require, "cjson")
if not cjson_ok then
  error("cjson module is required for docker metadata filter")
end

local metadata_cache = {}

local function read_file(path)
  local file = io.open(path, "r")
  if not file then
    return nil
  end
  local content = file:read("*a")
  file:close()
  return content
end

local function sanitize_label_key(key)
  if not key then
    return nil
  end
  local sanitized = key:gsub("[^%w_]", "_")
  if sanitized == "" then
    sanitized = "label"
  end
  return sanitized
end

local function load_metadata(container_id)
  if metadata_cache[container_id] then
    return metadata_cache[container_id]
  end

  local config_path = "/var/lib/docker/containers/" .. container_id .. "/config.v2.json"
  local payload = read_file(config_path)
  if not payload then
    return nil
  end

  local ok, data = pcall(cjson.decode, payload)
  if not ok or type(data) ~= "table" then
    return nil
  end

  local meta = {
    name = nil,
    image = nil,
    labels = {},
  }

  if type(data.Name) == "string" then
    meta.name = data.Name:gsub("^/", "")
  end

  local config = data.Config or {}
  if type(config.Image) == "string" then
    meta.image = config.Image
  elseif type(data.Image) == "string" then
    meta.image = data.Image
  end

  if type(config.Labels) == "table" then
    meta.labels = config.Labels
  end

  metadata_cache[container_id] = meta
  return meta
end

function add_metadata(tag, timestamp, record)
  local log_path = record["container_log_path"]
  if type(log_path) ~= "string" then
    return 1, timestamp, record
  end

  local container_id = log_path:match("/var/lib/docker/containers/([%w%-]+)/")
  if not container_id then
    return 1, timestamp, record
  end

  record["container_id"] = container_id
  local meta = load_metadata(container_id)
  if not meta then
    return 1, timestamp, record
  end

  if meta.name then
    record["container_name"] = meta.name
  end

  if meta.image then
    record["image"] = meta.image
  end

  for key, value in pairs(meta.labels or {}) do
    if type(key) == "string" and type(value) == "string" then
      local sanitized_key = sanitize_label_key(key)
      if sanitized_key then
        record["label_" .. sanitized_key] = value
      end
    end
  end

  return 1, timestamp, record
end

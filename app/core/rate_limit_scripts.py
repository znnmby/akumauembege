"""Atomic Redis Lua scripts for rolling quotas and recommendation locks."""


_ROLLING_LIMIT_SCRIPT = """
local member = ARGV[1]
local current = redis.call('TIME')
local now_ms = (tonumber(current[1]) * 1000) + math.floor(tonumber(current[2]) / 1000)
local retry_ms = 0
local denied = false

for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[2 + ((index - 1) * 2)])
    local window_ms = tonumber(ARGV[3 + ((index - 1) * 2)])
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        denied = true
        local blocking_rank = count - limit
        local blocking = redis.call(
            'ZRANGE', key, blocking_rank, blocking_rank, 'WITHSCORES'
        )
        if #blocking == 2 then
            local wait_ms = tonumber(blocking[2]) + window_ms - now_ms
            if wait_ms > retry_ms then
                retry_ms = wait_ms
            end
        end
    end
end

if denied then
    return {0, math.max(1, retry_ms)}
end

for index, key in ipairs(KEYS) do
    local window_ms = tonumber(ARGV[3 + ((index - 1) * 2)])
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
end

return {1, 0}
"""


_RECOMMENDATION_GATE_SCRIPT = """
local lock_key = KEYS[1]
local short_key = KEYS[2]
local long_key = KEYS[3]
local active_key = KEYS[4]
local owner = ARGV[1]
local member = ARGV[2]
local lock_ttl_ms = tonumber(ARGV[3])
local short_limit = tonumber(ARGV[4])
local short_window_ms = tonumber(ARGV[5])
local long_limit = tonumber(ARGV[6])
local long_window_ms = tonumber(ARGV[7])
local max_active = tonumber(ARGV[8])

if redis.call('EXISTS', lock_key) == 1 then
    local lock_retry_ms = redis.call('PTTL', lock_key)
    if lock_retry_ms < 1 then
        lock_retry_ms = lock_ttl_ms
    end
    return {2, lock_retry_ms}
end

local current = redis.call('TIME')
local now_ms = (tonumber(current[1]) * 1000) + math.floor(tonumber(current[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', short_key, '-inf', now_ms - short_window_ms)
redis.call('ZREMRANGEBYSCORE', long_key, '-inf', now_ms - long_window_ms)

local retry_ms = 0
local short_count = redis.call('ZCARD', short_key)
if short_count >= short_limit then
    local blocking_rank = short_count - short_limit
    local blocking = redis.call(
        'ZRANGE', short_key, blocking_rank, blocking_rank, 'WITHSCORES'
    )
    if #blocking == 2 then
        retry_ms = math.max(retry_ms, tonumber(blocking[2]) + short_window_ms - now_ms)
    end
end
local long_count = redis.call('ZCARD', long_key)
if long_count >= long_limit then
    local blocking_rank = long_count - long_limit
    local blocking = redis.call(
        'ZRANGE', long_key, blocking_rank, blocking_rank, 'WITHSCORES'
    )
    if #blocking == 2 then
        retry_ms = math.max(retry_ms, tonumber(blocking[2]) + long_window_ms - now_ms)
    end
end

if retry_ms > 0 then
    return {1, math.max(1, retry_ms)}
end

-- Admission and quota consumption are atomic across all API workers.
-- Expired leases recover capacity after a worker exits without cleanup.
redis.call('ZREMRANGEBYSCORE', active_key, '-inf', now_ms)
if redis.call('ZCARD', active_key) >= max_active then
    -- Completion can free a slot sooner than its crash-recovery deadline.
    return {3, 1000}
end

redis.call('SET', lock_key, owner, 'PX', lock_ttl_ms)
redis.call('ZADD', active_key, now_ms + lock_ttl_ms, owner)
redis.call('PEXPIRE', active_key, lock_ttl_ms)
redis.call('ZADD', short_key, now_ms, member)
redis.call('PEXPIRE', short_key, short_window_ms)
redis.call('ZADD', long_key, now_ms, member)
redis.call('PEXPIRE', long_key, long_window_ms)
return {0, 0}
"""


_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('DEL', KEYS[1])
end
return redis.call('ZREM', KEYS[2], ARGV[1])
"""

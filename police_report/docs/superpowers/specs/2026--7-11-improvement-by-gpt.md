结论：当前版本比上次健康很多，核心重构方向是对的，但仍有几个会直接造成“刷新看起来随机失败”的语义问题。尤其是当前实际状态已经进入：JWT 已过期、replay 返回 `THROTTLED`、且没有定时任务运行，因此现在无法自动获得可用 token。

## 当前检查结果

- 工作树干净，HEAD 为 `6a8d759`。
- 43 个测试全部通过。
- Python 编译检查通过。
- `.auth_replay.json`、`.token.json` 权限均为 `0600`。
- 当前 JWT 已于 **2026-07-11 11:34:31 CST** 过期。
- 最近一次刷新在 **14:15:55** 返回空 `200`，状态为 `THROTTLED`。
- 当前没有安装 `police_report` cron。
- 全局 Python 环境存在大量依赖冲突；项目目前依赖硬编码的 Miniconda base Python，不适合长期运行。

## 仍需优先修复的问题

### P1：写操作没有真正保证最小 TTL

[TokenProvider.get_token() (line 96)](/Users/ji/Projects/app-bot/police_report/token_provider.py:96) 接收 `min_ttl_s=600`，但刷新失败后，只要旧 token 还剩一秒，也会降级返回。

这意味着：

- 上传大文件时 token 可能中途过期；
- `submit_report` 虽然“提交前刷新”，但实际上可能拿到一个即将过期的 token；
- 方法注释所说的“fresh enough”与实际语义不符。

建议拆成两种模式：

```
get_token(min_ttl_s=600, allow_degraded=True)   # 普通只读请求
get_token(min_ttl_s=600, allow_degraded=False)  # upload / submit
```

写操作无法满足最小 TTL 时，应在发送前明确失败，绝不能冒险提交。

### P1：任何业务错误都会触发强制刷新

[WfjbClient._get() (line 118)](/Users/ji/Projects/app-bot/police_report/client.py:118) 捕获所有 `WfjbError` 后都会 `refresh(force=True)`。

所以以下情况都会错误消耗 replay：

- 参数错误；
- 服务端业务错误；
- 500/502 返回 JSON；
- 接口变更；
- 账号业务限制。

应让 `WfjbError` 携带 `http_status`、`business_code` 和 `auth_failure`，只有明确的 401、403 或已确认的 token 失效码才刷新。其他错误原样返回。

### P1：刷新结果分类仍过于武断

[token_[provider.py](http://provider.py) (line 146)](/Users/ji/Projects/app-bot/police_report/token_provider.py:146) 当前将：

- 所有非 200；
- 非 JSON 的 200；
- 所有业务码非 200

全部归类为 `PORTAL_EXPIRED`。

这会把 429、500、502、WAF 页面、临时上游故障都误报成 gsid 失效，诱导重新接手机抓包。

建议至少细分：

- `AUTH_EXPIRED`：明确的 401/403 或已验证业务码；
- `THROTTLED`：空 200、429；
- `UPSTREAM_ERROR`：5xx；
- `PROTOCOL_ERROR`：非预期响应；
- `NETWORK_ERROR`；
- `BAD_TEMPLATE`。

只有 `AUTH_EXPIRED` 才要求重新登录/抓包。

### P1：没有 throttle 熔断，重复调用会继续恶化限流

`.token_state.json` 已经记录了 `THROTTLED` 和时间，但 Provider 从不读取它。当前 token 过期后，每次运行 `whoami`、`refresh` 或其他命令都会再次 replay。

建议加入：

- `next_retry_at`；
- 指数退避，例如 5、15、30、60 分钟；
- 随机抖动；
- 普通命令尊重冷却时间；
- 仅提供显式 `refresh --bypass-backoff` 绕过；
- 记录模板指纹、成功次数和连续 throttle 次数，但不记录 secret。

README 中“一个 replay template 可以 indefinitely refresh”的表述也应删除。目前的实际状态并不支持这个结论。

### P2：状态文件故障会阻止保存新 token

当前顺序是：

1. replay 成功；
2. 写 `.token_state.json`；
3. 保存 token。

如果状态文件因权限、磁盘或目录问题写失败，一个已经成功拿到的新 token 会被直接丢弃。状态文件只是观测数据，不应阻塞关键凭据。

建议先原子保存 token，再 best-effort 更新状态；状态写失败只告警。

### P2：Portal OTP 流程仍丢失 Session

CLI 先调用 `request_otp()`，它创建一个 `requests.Session`；输入验证码后，`login_full()` 又创建另一个 Session，并重新获取公钥。

如果服务端开始通过 Cookie、验证码上下文或风控状态绑定 OTP 请求，这个流程会突然失效。

建议引入：

```
flow = PortalLoginFlow(phone, device_id)
flow.request_otp()
result = flow.complete(sms_code)
```

整个过程复用同一个 HTTP Session、公钥和 device ID。同时给 Portal 流程补完整的 mocked tests。

另外，[portal_[login.py](http://login.py) (line 196)](/Users/ji/Projects/app-bot/police_report/portal_login.py:196) 的直接运行入口仍会把 gsid、access token、refresh token 全部打印到终端，应改成脱敏输出。

### P2：token 仍有两个存储源

虽然 `_fresher()` 已修复旧 `.env` 遮蔽问题，但 `save_token()` 仍依次写 `.token.json` 和 `.env`，并不是真正的跨文件事务。

更简单可靠的方案：

- `.token.json`：唯一 token 状态源；
- `.env`：仅保存姓名、电话等非 token 配置；
- 更进一步，将 replay/session/token 放入 macOS Keychain；
- CLI 参数和环境变量只作为单次显式覆盖。

### P2：抓包范围仍过大

[mitm_[addon.py](http://addon.py) (line 34)](/Users/ji/Projects/app-bot/mitm_addon.py:34) 仍将代理经过的所有请求、响应和 header 写入 `/tmp/re/mitm.jsonl`，只是控制台隐藏了遥测。

建议：

- 磁盘层面只保存警务域名；
- 增加 `--auth-only`，仅保存 `wfjb.auth`；
- 文件创建强制 `0600`；
- 使用结构化的 `body_hex` 名称，替换误导性的 `req_body_b64`；
- 设置大小限制和轮转；
- 成功执行 `save-replay` 后可提示安全删除原始 capture。

## 针对“避免一直连接手机”的方案

建议设计可插拔的认证恢复链：

```
有效 JWT
  → frozen replay
  → replay template 池
  → ARM64 Android Emulator 自动生成新签名
  → 最后才人工介入
```

近期可落地方案：

1. 完成上面的严格 TTL、错误分类和 throttle 熔断。
2. 每次手机/模拟器会话捕获多个不同 `ts/sign` 的 auth template，组成可轮换池。
3. 在本机创建新的 ARM64 API 34 AVD；现有两个 AVD 指向已删除的 API 31 image。
4. 模拟器按需启动，运行官方 APK，通过 ADB/UI 自动打开 WFJB 页面。
5. mitmproxy 使用 `auth-only` 模式，仅等待一条新 `wfjb.auth`。
6. 保存模板后立即关闭模拟器。

这样可以完全取消长期连接物理手机。真正做到“连 live capture 都没有”，仍需要把 mPaaS Security Guard signer 封装成可调用的本地 signer，或者完成离线签名逆向；这是后续阶段，不应阻塞前面的可靠性改造。

## 建议实施顺序

1. 严格 TTL 与只刷新认证错误。
2. 修正 HTTP/业务错误分类。
3. throttle 熔断与状态历史。
4. 单一 token 存储源。
5. Portal 登录 Session 闭环及测试。
6. `auth-only` 抓包。
7. ARM64 模拟器自动恢复。
8. 最后再研究完全离线 signer。


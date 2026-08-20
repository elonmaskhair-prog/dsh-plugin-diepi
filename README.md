# dsh-plugin-diepi

`dsh-plugin-diepi` 是一个独立维护的社区 DSH 插件：用户用自然语言表达日线股票或 ETF
策略，DSH 负责对话与编排，`diepi-mcp` 提供受约束的工具协议，diePi 负责数据校验、因果边界、
撮合、记账与不可变结果工件。

```text
自然语言
  -> DSH + diepi-quant-research Skill
  -> StrategySpec v1（结构化、可预览、拒绝任意代码）
  -> diepi-mcp（MCP stdio、作业队列、证据绑定）
  -> 独立 diePi 子进程
  -> 已验证的 RunArtifact / ResultContract
```

当前版本是 Developer Preview：Python 包为 `diepi-mcp 0.1.0a1`，npm bundle 为
`dsh-plugin-diepi 0.1.0-alpha.1`。本项目不是 DeepSeek 官方项目，也不代表任何官方认可。

## 能做什么

- 日线、现金账户、单只 A 股或 ETF。
- `StrategySpec v1 / ma_crossover`：快慢均线严格交叉，可选成交额放大过滤。
- 盘前决策只读取截至 T-1 的完整日线，订单最早在 T 日开盘撮合。
- 支持 `raw_only`、`adjusted_only` 和严格对齐的 `dual` 数据。
- 回测异步排队，`submission_id` 使未知超时重试保持幂等。
- 数据来源、交易日历、策略模板、执行参数和最终 artifact 逐层绑定。
- DSH bundle 自动挂载八个受约束 MCP 工具与 `diepi-quant-research` Skill。

暂不支持任意 Python、分钟策略协议、做空、杠杆、期货、实盘、券商凭证、内置行情下载、
参数优化器或 GUI。模型不能通过本插件传入路径、命令、SQL 或 Python 源码。

## 五分钟 synthetic 体验

首次体验不读取本仓库或 diePi 仓库的真实行情。`diepi-mcp init-config` 在 DSH 工作区外
排他认领一个全新的 adapter home，并调用 diePi 的同一个 deterministic demo generator，
生成虚构价格、成交量、交易日和证券标签；这些数据只用于验证安装与协议接线。

要求：

- Python 3.10+；
- Node.js `^22.19.0 || >=24.0.0`；
- pnpm `11.7.x`，且 `pnpm` 在 `PATH`；
- DSH `0.1.0-rc.7`（主支持）或 `0.1.0-rc.8`（canary）。

先安装协调发布后的 Python Alpha，并创建一个新的 host-owned 根。`adapter-home-alpha1`
必须尚不存在：

```powershell
$hostRoot = Join-Path $env:LOCALAPPDATA "diepi-mcp-host"
$adapterHome = Join-Path $hostRoot "adapter-home-alpha1"
New-Item -ItemType Directory -Force -Path $hostRoot | Out-Null

python -m venv (Join-Path $hostRoot "venv")
$experiencePython = Join-Path $hostRoot "venv\Scripts\python.exe"
$mcpCommand = Join-Path $hostRoot "venv\Scripts\diepi-mcp.exe"

& $experiencePython -m pip install "diepi-mcp==0.1.0a1"
& $mcpCommand init-config --target $adapterHome

$env:DIEPI_MCP_COMMAND = (Resolve-Path $mcpCommand).Path
$env:DIEPI_MCP_CONFIG = (Resolve-Path (Join-Path $adapterHome "diepi-mcp.json")).Path
& $env:DIEPI_MCP_COMMAND --check-config
```

初始化命令拒绝覆盖、合并或复用已有目标。它先以排他方式创建目标和
`.diepi-mcp-incomplete` 标记，再以 exclusive-create 写入 staged payload，最后才提交
`diepi-mcp.json` 并移除标记。失败会保留一个没有可用配置的 incomplete 目录，绝不会删除
或接管竞态目标；请改用一个新目录，或由 host owner 核对内容后人工清理。生成的配置只含
相对路径，state、results 和 synthetic 数据都留在 adapter home，不会进入 Agent 工作区或
Git 仓库。
[examples/config.example.json](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/examples/config.example.json)
仅是 schema 参考，不需要手工复制。生产环境还应通过文件权限让 Agent 只读行情，并且不能
写配置、MCP 环境、state 或 results 根。

DSH 的插件管理会调用裸 `pnpm`。Node 自带 Corepack 时可固定 DSH 所用版本：

```powershell
corepack enable
corepack prepare pnpm@11.7.0 --activate
pnpm --version
```

若当前 Node 不包含 `corepack`，请先按 Corepack/pnpm 官方说明安装它，再继续。安装到
`web` profile，而不是只含 base bundle 的任意自定义 profile：

```powershell
dsh plugin --profile web add dsh-plugin-diepi@0.1.0-alpha.1
dsh --profile web --dump-config
dsh web --no-open
```

插件成员变更只对新 DSH 进程生效。若要使用 headless：

```powershell
dsh plugin --profile headless add dsh-plugin-diepi@0.1.0-alpha.1
dsh --profile headless --dump-config
dsh --profile headless
```

从本仓库 checkout 测试 bundle 时，在仓库根目录把包名替换为 `./dsh`。不要直接从 Git
仓库根 URL 安装 npm 包；npm package 位于 `dsh/` 子目录。

Bundle 在激活时强制要求 `DIEPI_MCP_COMMAND` 和 `DIEPI_MCP_CONFIG` 都是显式绝对路径。
它不会退回到 `PATH` 中名字相同的可执行文件，也不会猜测配置位置。
MCP 子进程的工作目录固定为 host-owned 配置目录，而不是 Agent workspace。

## 自然语言到回测

例如：

> 用 510300.SH 日线，5 日线上穿 20 日线时满仓，死叉清仓；回测 2024 年。

Agent 先生成可审阅的 `StrategySpec v1`，再依次调用：

1. `capabilities`
2. `preview_strategy`
3. `validate_data`
4. 使用一个新的、以 `req_` 开头的 `submission_id` 调用 `start_backtest`
5. `job_status`，必要时 `cancel_job`
6. `get_result`

发生 MCP timeout 或传输中断时，必须用完全相同的参数和同一个 `submission_id` 重试。
只有以下条件全部成立时才可以比较结果：

```text
artifact_verified == true
adapter_attribution_verified == true
result_committed == true
result_status == SUCCESS
rankable == true
```

## 自有行情与 Tushare Skill

最小输入是符合 diePi `market_data_v1` 的 raw 日线；最佳输入是完全对齐的 raw、HFQ 和原始
复权因子三轨。上游接口把数据称为 `hfq`，并不能证明它满足 diePi 的因子恒等式，最终以
diePi validator 为准。

插件不内置连接器、不携带真实行情、不保存 token，也不会自动安装外部 Skill。数据缺失时，
Agent 可以把用户引导到独立安装的
[Tushare 官方 Skill](https://github.com/waditu-tushare/skills)。我们只记录一个审阅过的上游
commit 作为人工交接参考；安装、网络权限、凭证、供应商条款和更新决定都由用户控制。详细
规则见
[Tushare handoff](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/dsh/skills/diepi-quant-research/references/tushare-data-handoff.md)。

外部 Skill 只负责把数据写入一个新的 staging 根。host owner 必须在会话外校验、注册为
不透明 `dataset_id`、冻结数据根并重启 MCP；单次 Agent 会话不能跳过这一边界。

## 版本与兼容策略

- `diepi-mcp==0.1.0a1` 精确依赖 `diepi==0.1.1`。这是有意的：adapter 当前依赖 diePi 的
  取消回调、来源证据、日历身份和原子 artifact 语义，宽泛的 `0.1.x` 范围不能证明兼容。
- DSH rc.7 是 Alpha 主支持版本；rc.8 是前瞻 canary。两者仍处于 Developer Preview，
  rc.8 测试应使用独立的 `DSH_HOME`，不要复用 rc.7 会话状态。
- Harness 自带 `@deepseek-ai/dsh-mcp-client` 和 `@deepseek-ai/dsh-skill-filesystem`；本 bundle
  不重复携带它们。

发布顺序的硬门禁是先让 `diepi==0.1.1` 可从协调的 Python index 安装；插件 CI 在此之前应当
失败，而不是放宽 pin。随后发布门禁会校验下载候选的 SHA-256、在干净环境安装 sdist，并在
Windows/Ubuntu、Node 22/24、web/headless、DSH rc.7/rc.8 四格中安装 wheel 与打包后的 npm
tgz。每格分别执行 wheel 上的直接 MCP/adapter synthetic smoke，并真实启动 DSH，在 Loader
settle 后严格发现八个 `mcp__diepi__*` 工具且调用 `capabilities`。该无模型 key 探针不声称覆盖
经 DSH 的取消、崩溃重连或真实模型效果；这些边界由源码测试或单独记录的手工发布验证承担。

## 安全边界

MCP stdio 是 host 进程，不在 Agent 文件沙箱内。配置、可执行文件、数据根、state 和 results
必须由 host owner 控制。哈希证明工件之间的身份一致性，但不能把一个 Agent 可写目录变成
可信证据根，也不能证明行情真实、获得合法授权或没有幸存者偏差。

详细威胁模型与私密漏洞报告入口见
[SECURITY.md](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/SECURITY.md)。

## 开发

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev]"
.\.venv\Scripts\python.exe -m pytest -m "not integration"
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist\*

Push-Location dsh
npm pack --dry-run
Pop-Location
```

贡献规则见
[CONTRIBUTING.md](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/CONTRIBUTING.md)，
版本变化见
[CHANGELOG.md](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/CHANGELOG.md)。

## 免责声明与许可证

本项目仅用于软件工程验证、量化研究和历史回测。输出不构成投资建议、交易邀约、收益保证或
券商服务；回测可能受到数据质量、幸存者偏差、复权、撮合假设、费用、滑点和过拟合影响。
用户对数据来源、供应商许可、模型使用和投资决定承担责任。

Apache-2.0，见
[LICENSE](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/LICENSE)。第三方关系见
[THIRD_PARTY_NOTICES.md](https://github.com/elonmaskhair-prog/dsh-plugin-diepi/blob/main/THIRD_PARTY_NOTICES.md)。DeepSeek、DeepSeek Harness 和 DSH 的
相关名称与标识归其各自权利人所有；本项目与其没有隶属或背书关系。

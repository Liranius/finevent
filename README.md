# finevent

文中的域名和部署路径已替换为示例值；实际环境映射仅保存在不提交的 `.deployment.local.json`。

个人财经事件工作台。技术方向为 Vue 3 PWA、Go 单进程和独立 SQLite，复用现有 TrendRadar 输出；云端 AI 使用 DeepSeek，已部署并通过本批单次真实业务验收。

**新闻事件流、跨源证据关联与规则分类已部署并通过本批验收，覆盖迁移、重启、两端页面及 65 分钟采集和资源观察；此前日历与自动提醒发布已验收。** 日历版本的两端真实送达和事件详情跳转、生产迁移、备份、服务重启及六小时十五分钟运行观察通过，保留 BLS 主源不可用及纽约联储覆盖限制。日历提供月视图、议程、原文及修订记录；基金公告先生成规则提取草稿，人工确认后入历，真实公告验收待用户实际使用时补充。保留登录、设备推送测试、快讯与 TrendRadar 采样、时效统计和资源记录。新闻页提供筛选、来源全文、修订、分类依据及未知时间状态；接口与关联边界见 [新闻接口](docs/news-api.md)。DeepSeek 业务解读及月预算控制已部署，本批真实调用、费用核算、资源和双端显示验收通过，V2EX 单次失败及恢复证据保留；持续自动解读由用户在设置中开启，新闻关注与重要事件推送已完成本批验收，包含迁移、重启、新闻 API、资源观察、固定前两轮来源恢复及双端真实通知；后续 6 次来源失败保留为持续运行问题，边界见 [新闻推送接口](docs/news-push-api.md)。完整阶段及验收记录见 [实施状态](docs/implementation-status.md)。

来源健康展示已部署并完成本批签收：迁移、13 条历史异常重启保全、双端页面、完整功能/资源观察及固定前两轮来源恢复检查通过；财联社电报的失败与恢复证据保留。分别显示 TrendRadar 各来源的成功、失败、恢复、缺记录与过期状态，故障历史保留 30 天。入口与范围见 [来源健康接口](docs/source-health-api.md)。

新闻与日历的人工关联已部署并完成本批验收：迁移、重启、接口、65 分钟采集与资源观察及双端关联显示/跳转通过。支持查找日程、确认依据、双向查看、修订后重新核对及解除；生产首次重启时关联为空等验证边界见 [关联接口](docs/calendar-links-api.md)。

## 初始化

自有代码采用 [MIT 许可证](LICENSE)；收录的第三方技能和 schema 遵循各自许可证，来源与范围见 [第三方材料说明](THIRD_PARTY_NOTICES.md)。

前端位于 `apps/web/`，由 pnpm workspace 管理；后端位于 `apps/server/`，保留独立 Go module。`e2e/` 验证前后端集成，`deploy/` 保存部署工具及测试，`scripts/` 提供仓库级构建与发布入口。公共检查配置和锁文件位于根目录。

以下命令从仓库根目录执行。Node 版本以 [.node-version](.node-version) 为准；pnpm 版本见 [package.json](package.json)，Go 版本见 [go.mod](apps/server/go.mod)。使用现有锁文件：

```powershell
fnm use
pnpm install --frozen-lockfile
pnpm hooks:install
go -C apps/server mod download
pnpm exec playwright install chromium
```

本地运行完整构建，与生产保持同一个 HTTP 入口：

```powershell
pnpm build
pnpm server -init -origin http://127.0.0.1:8787
pnpm server
```

打开 <http://127.0.0.1:8787/finevent/>。初始化交互式读取密码，不回显；不会覆盖现有配置。`var/config.json` 包含密码散列与 VAPID 私钥，`var/` 不提交版本控制。

`pnpm server` 在 `apps/server/` 执行 Go，默认读取仓库根目录的 `var/` 和前端产物 `apps/web/dist/`。额外传入的相对文件路径以 `apps/server/` 为基准。

需要 Vite 热更新时，先运行 `pnpm server -init -data ../../var/dev -origin http://127.0.0.1:5173` 初始化独立数据目录，再在两个终端运行 `pnpm server -data ../../var/dev` 和 `pnpm dev --host 127.0.0.1`。自定义含密钥的数据目录应放在仓库根目录已忽略的 `var/` 下。开发模式不注册 Service Worker；真实 PWA 测试使用完整构建。

`pnpm build` 检查前端和仓库工具类型，并将前端产物写入 `apps/web/dist/`。仅操作前端子项目时可使用 `pnpm --filter @finevent/web build`、`pnpm --filter @finevent/web test:unit`；Go 命令使用 `go -C apps/server <命令>`。发布包继续包含顶层 Go 程序、`dist/` 和 `deploy/`，供现有服务和安装器使用。

公共环境默认值位于 [apps/web/.env](apps/web/.env)，本地开发、构建与 CI 共用。`VITE_APP_TITLE` 控制浏览器标签页标题；个人覆盖可参考 [示例](apps/web/.env.example) 创建同目录的 `.env.local`，无需复制即可运行。`VITE_*` 会进入客户端产物，只能填写公开配置。Vite 开发服务集成 Vue DevTools；编辑器推荐见 [.vscode/extensions.json](.vscode/extensions.json)。

前端使用 Vue Router 管理工作区与深链接，采用 hash 模式，继续使用现有 `/finevent/` 静态入口；通知的旧地址仍可使用，导航行为见 [设计规范](DESIGN.md#行为)。TODO: Pinia 暂缓引入，当前共享状态仍由父组件与 props/emits 管理；若跨工作区共享和更新明显增多，再评估 store 边界与迁移，避免提前引入正文缓存和额外清理职责。

`-collect` 开启正常每两分钟快讯采样及每六小时日历同步；快讯失败重试和窗口证据见 [新闻接口](docs/news-api.md#采集重试与延迟证据)。`-trendradar <output目录>` 配合 `-collect` 开启每分钟只读导入。普通启动默认不请求外部来源。日历同步单独调度，来源失败保留已有日程，状态在设置的「日历官方来源」中展示；重启延续上次同步时间。首次服务器同步中 FOMC、BEA 成功，统计局读取响应失败、BLS 返回 403；当前采用已获用户确认的纽约联储补充来源＋统计局官方快照，主源故障继续展示。该方案的来源及长时验收已完成，保留 BLS 不可用与覆盖范围限制，证据见 [实施状态](docs/implementation-status.md)。

10 月 31 日终态报告显示：统计局在下一轮直接同步成功，导入 47 项，FOMC、BEA 再次成功，BLS 仍为 403；六小时十五分钟资源与运行检查通过。新版会保留已有统计局日程，较旧快照不会覆盖它们，来源时效以实际最后成功记录为准。

同日纽约联储月末修复已在服务器生效，沿实际相邻月份链接取回当月/下月日程，成功导入 11 项，事件总数 85。新观察保留既有统计局直接同步数据，旧快照补入为零；已中断的前序观察不计为通过。

`-collect` 启动时会校验并导入随程序附带的统计局官方快照；独立使用 `-calendar-snapshot` 可在不联网的本地服务导入。快照仅补缺，不覆盖已有日程或改变主源同步状态，不能保证自动获得后续改期。维护依据见 [快照说明](apps/server/internal/calendar/snapshots/README.md)。

日历保留原始时区，有明确时刻的事件显示北京时间；仅日期的事件保留原始民用日期，不换算成 UTC 零点。基金窗口日期包含结束日，交易日和日内时刻等限制保留在条件中；缩写、相对日期及多份额公告需要人工补全。新日历表以增量方式创建，保留既有新闻、会话和探测账本；事件与修订不参与普通新闻的九十天清理。API 见 [日历接口](docs/calendar-api.md)。

“提醒设置”已部署：选定已连接的接收设备、类别/基金或单个事件后开启自动提醒。默认提前 24 小时、30 分钟；仅日期事件默认前一天和当天北京时间 09:00，基金可增加开放最后一天提醒。默认关闭，升级不会自动发送。改期取消和关注变更撤销旧计划，发送前复核最新状态；过期不补发。设置页展示待发送计划和服务接受/设备回执/点击记录，回退版本会暂停自动提醒并保留历史。具体字段、有效期与重试边界见 [自动提醒接口](docs/calendar-api.md#自动提醒)。生产首次迁移、重启一致性、电脑接收、小米锁屏显示及两端点击对应详情已通过，新版本长时运行与资源观察已签收。

## 验证命令

AI 功能与配置见 [AI 解读接口](docs/ai-api.md)。默认关闭；开发环境通过 `pnpm server -configure-ai` 隐蔽录入密钥，重启后再在页面开启。没有密钥时可运行全部本地回归，模型测试使用夹具，不产生真实费用。生产本批单次真实调用、费用及并发资源已签收，可在“AI 解读”启用；“自动解读新的重要报道”只处理开启后新入库的重要报道，月预算 30 元。持续批量负载尚未长期验收。

```powershell
pnpm ci:format
pnpm ci:lint
pnpm test:unit
pnpm test:go
pnpm vet:go
python -m unittest discover -s deploy -p 'test_*.py'
pnpm test:e2e
git diff --check
```

`pnpm test:e2e` 包含类型检查、前端构建和真实 Go 服务的 Chromium 检查，覆盖桌面、窄屏、键盘登录、退出、离线资源、通知记录、日历切换、公告确认及改期取消。模拟的通知记录不属于 Web Push 送达验收；真机步骤见 [验收记录](docs/implementation-status.md)。截图位于 `test-results/`，失败保留 trace。

`pnpm test:unit` 和 `pnpm test:watch` 同时覆盖前端与根目录知识工具测试。三浏览器回归使用：

```powershell
pnpm exec playwright install chromium firefox webkit
pnpm test:e2e:all
```

浏览器测试入口先构建前端，再为 Chromium、Firefox、WebKit 分别启动端口 4173、4174、4175 的独立 Go 服务及数据库，避免跨浏览器残留数据。每个浏览器内的测试共享该服务，保留单 worker 和默认无头运行；需要可见浏览器时传入 `--headed`。接口替身测试阻止 Service Worker，真实离线测试单独启用。截图按测试和浏览器分别保存在 `test-results/` 子目录中。

离线用例另起专用服务，在停止源站后验证真实缓存回退；WebKit 离线模拟器的已知限制及恢复条件见 [测试旁的 TODO](e2e/workspace.spec.ts)。

构建将离线资源清单的内容版本写入 `sw.js`，使应用资源变化也能触发浏览器的 [Service Worker 更新检测](https://web.dev/learn/pwa/service-workers)。相同内容重复构建保持一致；已有 PWA 通过“更新应用”切换缓存，浏览器回归覆盖从旧 Worker 更新后断网加载新版。

一次真实来源连通性检查可显式设置 `FINEVENT_LIVE_TEST=1` 后运行 `go -C apps/server test ./internal/probe -run TestLiveWallstreet -v`。该检查不证明两个交易日的时效。

## 部署验证服务

DeepSeek 首次业务发布使用 `scripts/publish-ai.ps1 -Archive <已核验发布包> -AfterReview <上一已签收操作 ID>`。脚本在 SSH 终端隐蔽录入密钥，然后后台安装、检查账本迁移与重启，等待一次正常 TrendRadar 采集（最多 32 分钟）并提交一条真实新闻解读；继续观察五分钟正常采集，再核对费用和解读重启保全。自动解读保持关闭，不自动重试付费请求。测试期间先不要在页面启用 AI 或请求解读，结束后按报告验收；日历和新闻可继续使用。进度及终态只读下载：`scripts/publish-ai.ps1 -Report`。详细边界见 [AI 发布验收](docs/ai-api.md#发布验收)。

新闻及新闻推送批次使用 `scripts/publish-news.ps1`，与日历发布共享操作记录、备份、回滚和服务器锁。先运行 `scripts/build-release.ps1`，审核上一操作的终态后，使用明确的 `-Archive` 和 `-AfterReview` 发布。取回进度或终态：

```powershell
.\scripts\publish-news.ps1 -Report
```

新闻专项先核对旧数据及修订保全、分类积压（最多等待二十分钟）、真实 API、临时会话清理及重启恢复，再观察约六十五分钟，覆盖至少两轮 TrendRadar 采集、新闻导入和资源。资源阈值沿用既有规则；临时 API 会话只在服务器本机使用，最长两分钟且正常/异常退出均清理，不创建测试新闻或基金事件、不调用模型。日历下个六小时周期不在本次专项复测范围内，既有 BLS 限制继续保留。首次报告进入 `observing / news_and_resources` 后再进行页面验收；`queued/deploying` 仍是进度。源码与本地测试通过不等于生产验收通过。

日历版本使用新的独立发布入口，旧的 `verify-server.ps1` 仅用于历史证据，不用于本轮验收。先完成本地检查和构建，再运行：

```powershell
.\scripts\build-release.ps1
.\scripts\publish-calendar.ps1 -AfterReview <上一轮完整ID>
```

新流程核对已接受的 `trendradar-persist-…` 报告、迁移后内存记录、当前容器、父级 slice 和只读补丁。安装复用下述备份与回滚机制，另将冻结备份恢复至一次性数据库验证可读性；随后重启一次 finevent，比较日历事件、草稿、修订和同步进度。生产表不会写入测试基金公告，不调用模型，也不改变 TrendRadar。

发布和约六小时十五分钟的观察由服务器后台任务执行，SSH 断开不停止它。首次下载的 `queued/deploying/observing` 只是进度。新流程要求五个来源均再次尝试，FOMC、BEA、纽约联储成功，统计局直接同步成功或有摘要匹配且仍覆盖未来的已核对快照；保留统计局/BLS 的真实错误。满足此范围及原有内存、TrendRadar 导入与 HTTPS 检查后产生 `completed_with_source_limits_for_review`，仍需人工核对报告和真机页面，不表示四个主源完整恢复。进度与终态均使用同一条命令取回，不重新部署：

```powershell
.\scripts\publish-calendar.ps1 -Report
```

先取回并审查服务器最新操作的终态报告，再以 `-Archive <新包路径> -AfterReview <上一轮完整ID>` 启动后续版本。服务器记录是权威来源，本地指针仅供找回操作和诊断；服务器再次核对前序是最新操作、原观察已结束、当前版本对应、备份/重启/资源/TrendRadar/HTTPS 通过，且没有超出统计局/BLS 限制的未解决故障。经核对已恢复的来源失败，可在 `deploy/calendar-reviews.json` 记录原报告与独立恢复证据的摘要及审核原因；仅匹配的这一轮可进入后续发布，不改写原报告，也不放宽资源、服务或来源同步检查。正在观察或已回退的报告不能放行；删除本地指针不能绕过检查。

若终态报告只有 TrendRadar 来源失败需要核对，执行 `.\scripts\inspect-calendar-observation.ps1`，从现有 SQLite 读取观察期间失败的来源、时间和后续恢复记录，单独保存报告及原报告摘要。它不启动新采集、不修改原观察或生产数据库，也不自动签收；缺失数据库、计数不一致和再次失败会明确保留。下载中断后用同一脚本的 `-Report` 取回证据。

自动提醒首发仍默认关闭。发布流程检查新增表、引用完整性、初始零任务，并在重启前后核对设置、关注和任务标识；设备送达另行验收。安装与短暂重启完成后可操作新版，六小时来源与资源观察在后台继续，不必等它结束才测试提醒。

已明确授权提前发布修复时，改用 `-Archive <新包路径> -ReplaceObservation <原观察ID>`。服务器先保存旧报告原始副本，核对其最近检查点、当前程序与配置、备份/重启、内存和服务健康，再仅停止该观察进程。未完成观察标记为 `interrupted_for_repair`，不算通过；原应用在正常安装前继续运行。新版本独立重新观察，期间应用可以使用；首份报告可能仍为排队状态，使用 `-Report -RunId <操作ID>` 继续查询，SSH 中断不停止后台任务。该入口不能替代对资源或部署故障的审查。

本地报告在 `.validation/calendar-release-*/report.json`，原始样本、数据库快照和回滚依据保留在服务器对应 `/srv/example/finevent-backups/` 目录。发布后发生资源或服务故障会尝试回退，并用最新数据库保留新增业务数据；来源不可达单独标记待审查。报告含回滚命令，操作前核对当前状态；独立改动导致回滚拒绝时保留证据，不盲目重试。应用界面更新后，在手机和电脑检查日历、公告确认、改期和取消；PWA 出现“更新应用”时点击更新。

首次同步的统计局读取失败与 BLS 403 使用以下只读诊断定位；不重新发布、不修改数据库或重启应用：

```powershell
.\scripts\inspect-calendar-sources.ps1
```

该命令本地编译诊断程序，上传后在服务器后台对四个固定官方地址各请求一次，沿用业务请求的 15 秒限制，记录状态码、耗时及正文读取错误类型。BLS 年度网页仅作候选连通性检查。报告位于 `.validation/calendar-source-check-*/report.json`；SSH 断开或下载失败后使用 `.\scripts\inspect-calendar-sources.ps1 -Report` 取回同一报告，不重复请求。该诊断不会修改正在运行的六小时观察结果。

统计局年度页面曾被首次发布的 15 秒期限截断，60 秒独立候选也曾超时，后来原版定时同步又成功。10 月 31 日补充来源版本已部署独立 60 秒日历请求，快讯仍为 15 秒，正文仍限 2 MiB；延长期限不能保证来源可用。`.\scripts\inspect-calendar-sources.ps1 -Candidate` 只读取网络，不安装修复；旧报告保留在各次验证目录，`-Report` 可下载已有报告。显式设置 `FINEVENT_LIVE_CALENDAR=1` 后运行 `go -C apps/server test ./internal/calendar -run TestLiveNYFedSupplement -v` 可将实时纽约联储页面和内置快照导入临时测试库，不改生产数据。

所有运维入口先读取本地部署配置：

```powershell
Copy-Item .deployment.example.json .deployment.local.json
# 在本地文件中填写 SSH 目标、网站域名及各部署目录，再构建发布包。
```

`.deployment.local.json` 不提交，也可用 `FINEVENT_DEPLOY_CONFIG` 指定仓库外的配置文件。源码中的域名和绝对部署路径为示例值；本地发布包和临时上传文件按配置渲染，保存在已忽略的 `release/`、`.validation/`。`-SshTarget` 可覆盖本次连接目标。本地默认构建、上传或 SSH 在配置缺失或非法时停止；通用构建不读取私有部署配置。站点和目录配置变化后重新渲染通用包即可，不必重新编译；不要公开发布带实际环境值的产物。配置与上传参数的本地回归为 `pwsh -NoProfile -NonInteractive -File scripts/test-deployment-config.ps1`，使用模拟命令，不连接服务器。

### 通用产物与本地发布

`build-release.ps1` 使用当前 Node/pnpm 环境，版本按 `.node-version` 与 `package.json` 准备，无需在脚本中调用 fnm。默认命令继续生成 `release/finevent-<时间>.tar.gz`，现有 `publish-calendar.ps1`、`publish-news.ps1` 等发布入口不变。它先构建通用包，再用本地配置渲染部署文件；Go 程序和前端文件在渲染时保持不变。

只构建可保存到 CI 的通用包，或将下载的通用包转换为本地发布包：

```powershell
.\scripts\build-release.ps1 -Generic
python scripts/check-release.py release/generic/finevent-<时间>.tar.gz --kind generic
.\scripts\render-release.ps1 -Archive release/generic/finevent-<时间>.tar.gz
# 继续沿用已有发布及前序验收参数：
.\scripts\publish-calendar.ps1 -Archive release/finevent-<时间>.tar.gz -AfterReview <上一轮完整ID>
```

通用包位于 `release/generic/`，只含应用文件、部署模板和公开元数据，不含私有部署配置。`release.json` 记录实际检出的提交 SHA、工作区是否有未提交修改及目标平台；PR 的 SHA 是被测试的合并结果。包内 `sha256.json` 覆盖所有文件，包旁 `.sha256` 校验压缩包本身；渲染后记录原通用包摘要并重新计算文件摘要。下载后核对 Actions 运行、提交和包摘要；摘要用于完整性核对，不能代替来源确认。通用包未经渲染不能通过发布校验或安装器检查，旧格式部署包继续兼容。

`render-release.ps1` 可通过 `-ConfigPath` 指定配置、`-OutputDirectory` 指定输出目录，不覆盖已有发布包。`pwsh -NoProfile -NonInteractive -File scripts/test-release.ps1` 使用最近的通用包和虚构配置验证渲染与失败路径，不连接服务器。CI 在同一检出目录完成浏览器测试与构建后使用 `-SkipFrontendBuild` 复用该前端产物；普通本地构建不需要此参数。

### SSH 初始化与 Actions 部署

已有服务首次接入时，在本机终端执行：

```powershell
.\scripts\bootstrap-deployment.ps1 -PrepareOnly
.\scripts\bootstrap-deployment.ps1
```

脚本读取私有部署配置，生成独立 Ed25519 部署密钥，从本机已信任的 `known_hosts` 提取该主机记录，存放到 `.validation/deploy-access/`。Windows 限制该目录为当前用户访问，Linux 使用目录 0700、凭据 0600；重复执行保留原密钥。准备阶段不联网，安装阶段由用户在自己的终端输入现有 root SSH 密码。首次未知主机须先通过服务器控制台核对指纹，不使用未经验证的 `ssh-keyscan` 输出。

服务器安装 `/usr/local/sbin/finevent-release` 固定入口及 `/usr/local/lib/finevent-release/` 控制器，将新公钥以 `restrict,command=...` 追加到 root 的 `authorized_keys`，保留既有登录密钥和备份。该密钥不能用于通用 shell、SCP、PTY 或端口转发；产物通过固定入口上传。控制器会运行部署包内的安装代码，因此它仍是具有 root 发布权限的凭据，必须作为生产 Secret 管理。初始化不部署应用、不修改数据库、不重启服务；末尾通过专用密钥执行只读预检。首次装机及首次 AI 密钥录入保持独立交互流程。

本地日历/新闻发布和 Actions 共用 `release-client.py` 与服务器控制器。默认使用上述凭据文件，也可指定 `FINEVENT_SSH_KEY_FILE`、`FINEVENT_KNOWN_HOSTS_FILE`。服务器在执行前校验压缩包及文件摘要、前序验收，并持久保存操作和不可变发布包；本地与 Actions 共享注册锁及原有后台观察锁。相同 ID、内容和选项只返回已有状态；内容变化拒绝复用该 ID。启动结果不明或观察进程异常退出时保留 `needs_recovery`，需检查服务器证据，不自动重发安装。

GitHub 的 `production` environment 仅允许 `main` 分支，配置三个 environment Secrets：

| Secret                   | 内容              |
| ------------------------ | ----------------- |
| `FINEVENT_DEPLOY_CONFIG` | 完整私有部署 JSON |
| `FINEVENT_SSH_KEY`       | 专用私钥文件内容  |
| `FINEVENT_KNOWN_HOSTS`   | 已核对的主机记录  |

手动运行 **Deploy**，提供成功的 main push **CI** 运行 ID、已审核的最新服务器操作 ID，以及 calendar 或 news 验证范围。流程核对同仓库、CI workflow、main 历史、artifact 来源与 GitHub 摘要、包摘要和干净提交 SHA，再临时渲染私有包并启动服务器观察；PR 产物、其他 workflow、过期产物或不匹配 SHA 均拒绝。生产 Deploy 串行且不取消正在执行的部署。`Re-run jobs` 保持相同操作 ID；重新 dispatch 是新操作，不能用来重试结果不明的安装。AI 付费验证不在 Actions 选项中。

手动运行 **Report**，填写具体操作 ID 或 `latest`。它仅查询既有报告，不启动安装、采集或模型调用；日志和摘要不包含完整服务器报告、地址及路径。本地 `publish-calendar.ps1 -Report -RunId <操作ID>` 可将完整私有报告下载到 `.validation/<操作ID>/report.json`，不依赖原发布电脑的指针。后台观察超出 job 生命周期，绿色的启动/查询 job 不代表生产验收完成。服务器保留完整报告、备份、回滚依据和控制器私有日志；恢复前核对当前状态。

已完成专用 SSH 初始化、production Secrets、真实 Report、非法 CI 来源拒绝及同一 Deploy 重跑幂等验证。首次生产 Deploy 被旧固化证据的七天有效期拦在安装前；随后通过只读基线复验，以显式恢复完成实际安装，原失败记录保留。生产 PWA 更新、主要导航与旧深链接、手机通知点击和 65 分钟新闻/资源观察已通过本批验收，最终 Report 成功；证据与范围见[验收状态](docs/implementation-status.md#actions-部署验收)。

固化证据过期时，可用 `pwsh -NoProfile -File scripts/verify-baseline.ps1 -Archive <已核对来源的环境渲染包>` 启动 65 分钟只读复验；使用 `-Report -RunId <persistence-check-ID>` 下载完整报告，Actions **Report** 也接受该 ID。复验不重建容器、不重启服务、不触发采集或模型请求；核对原固化文件/容器/配置、连续资源采样、至少两轮正常采集及 HTTPS。它明确保留旧记录的保留期缺口，只为新观察窗口及之后提供证据。报告完成并审核后，发布显式传入 `-BaselineId` 与 `-BaselineSha256`（Actions 对应 `baseline_id` / `baseline_sha256`）；七天有效期、窗口结束后的连续历史及现有资源门禁仍生效。

若最新部署明确在安装前失败，审核原报告后可同时传入 `-RecoverPreflight <失败ID>`、`-FailedReportSha256 <报告摘要>`，`-AfterReview` 仍指向前一个已完成验收的操作；Actions 使用相应的 snake_case 参数。仅接受已停止、未进入安装、无安装/回滚/观察替换痕迹的 calendar/news 失败；重新复核原前序验收及当前版本，保留失败记录，以新 ID 登记恢复操作。报告变化、进程仍活动、安装结果不明或 AI 初始化失败均不能用此入口放行。服务端控制器需升级到协议 2：沿用已有专用密钥再次运行初始化脚本即可，不改变生产应用。

### 原验证版安装与共用机制

在项目根目录使用 PowerShell：

```powershell
.\scripts\build-release.ps1
.\scripts\deploy-probe.ps1
```

构建在本地完成，上传 linux/amd64 程序及静态资源。SSH 与 SCP 使用已有主机指纹，可能分别要求输入服务器密码；只在本机终端输入。首次部署另外设置 finevent 的独立访问密码。

安装器校验文件摘要，定位现有域名唯一的 HTTPS server 块，只加入 `/finevent/` 的独立 location include。创建受限的 finevent 系统账户和 systemd 服务，回环监听 8787；限制 96 MiB 内存、无 Swap，Go 内存目标 64 MiB。不会启动或重启 TrendRadar。

登录及安装 PWA 统一使用 `https://www.example.com/finevent/`。Nginx 将其他站点别名下的 `/finevent/` 请求重定向到该地址，保留路径与查询参数；部署检查会验证不带 `www` 的入口确实跳转。应用继续严格校验请求来源。

安装器发布前检查可用内存和磁盘、备份既有 finevent 数据库与配置；启动后检查一分钟资源和原 TrendRadar 入口，再启用服务。失败恢复旧程序与 Nginx 配置，保留失败证据。日历发布入口在此基础上执行上述更长的观察。报告会给出精确回滚命令，手工回滚会拒绝覆盖部署后另行修改的配置。

Nginx 重载后，每个 HTTPS 入口最多检查六次，失败间隔一秒；每次请求最长五秒。检查直连本机并校验证书，不受代理和 curl 用户配置影响。`https-checks.json` 在本次备份目录内保留状态码、响应摘要和重试记录；持续失败时另外记录 `local-health.json` 后回滚。终端会显示最后一次错误，便于区分服务故障与 HTTPS 路由故障。

如果 finevent 用户不能读取 TrendRadar 数据，安装器停止且不修改其权限。需在服务器对 `/srv/example/trendradar/output/news` 给予该用户只读权限和父目录穿越权限，确保未来日期数据库也可读，再重试。不要给予该用户 TrendRadar 配置或写入权限。

每日数据库备份保留七份；使用 SQLite backup API，包含 WAL。数据和私钥位于 `/srv/example/finevent-data`，发布目录位于 `/srv/example/finevent/releases`。状态页能看到实际采样；访问地址为 <https://www.example.com/finevent/>，部署完成前该地址不可作为可用结果。

## 服务器后续验收

2026-10-30 17:33 固化验证完成，报告 `trendradar-persist-…` 已审核通过，无需重新运行固化。新容器完成重建和重启；两轮九源采集、HTML、56 次 finevent 导入与两个 HTTPS 入口均正常。721 个样本中主机最低余量 267.52 MiB，无新增 OOM。首次空 IPAM 误判及回退证据完整保留。

当前保留原镜像、九个来源、功能开关及网页。补丁以主机文件只读挂载，Compose 持久化 192 MiB 内存和 256 MiB Swap，父级 `trendradar.slice` 设置 160 MiB 提前回收边界，主机 `vm.swappiness=60` 已持久化。原 Compose 内容在同目录 `finevent-trendradar-base.yml`，入口文件引用并覆盖上述设置。原容器已停止并保留作回退。

如需重新下载同一份终态报告：

```powershell
.\scripts\persist-trendradar.ps1 -Report
```

报告位于 `.validation/trendradar-persist-*/report.json`，最新路径见 `.validation/trendradar-persist-latest.json`。服务器 `/srv/example/finevent-backups/<运行编号>/` 保存原文件、数据库快照、状态及回退命令。回退恢复原容器和配置，保留新增 SQLite/HTML 数据；独立修改或恢复失败会标记待审查。已完成的观察任务退出是正常行为，当前生产容器继续运行。

此流程验证容器重建、重启及自启配置，尚未进行整机重启验收。补丁绑定当前 6.11.0 镜像，升级前须重新核对源码并验证，见 `deploy/trendradar_persist.py` 的 TODO。旧验证命令固定了原容器身份，仅保留用于追溯，固化后不能直接复用其资源准入结论。

以下保留已完成的验证流程，供追溯运行方式和报告含义：

```powershell
.\scripts\build-release.ps1
.\scripts\verify-server.ps1 -WithAI `
  -ResourceTrialId (Read-Host '已审核的资源调整报告 ID') `
  -OverlapAfter (Read-Host '已审核的验证报告 ID')
```

脚本先在线备份并只读统计历史数据，再安装验证包。`-ResourceTrialId` 指定已审核的生产调整报告；核对其终态、两轮采集、补丁、容器身份、资源策略及 OOM 计数，并检查从该观察起点到当前的连续资源记录。旧历史仍完整保留；只有新窗口无缺失、计数重置、新增 OOM 或低于 64 MiB 的样本，且当前余量至少 128 MiB，才能进入部署。不是跳过历史失败，未提供此参数时仍采用原历史门禁；真实 AI 验证必须提供该编号。

`-OverlapAfter` 关联已审核的上次验证报告，要求其 AI、资源准入和回滚均通过，且当前程序目录与启动时间仍匹配。该次演练造成的受控重启已有完整记录，因此连续资源窗口从其完成时刻继续，旧窗口及 OOM 证据保留；不明重启仍拒绝放行。此模式沿用上次回滚证据，不重复演练，但新包部署仍备份并在失败时自动回退。

`-WithAI` 会在 SSH 终端隐蔽读取 DeepSeek API Key，输入完成后再次核对资源，再安排一次与 finevent 正常采集并行的真实请求。请求运行于 finevent 服务进程及其 96 MiB 限额内，使用虚构测试资料、非思考模式和最多 1024 个输出 token。每次预留 0.02 元，计入 30 元月预算；不自动重试。使用可更新的高峰单价保守记账，用量不明时保留预留额。密钥仅短暂保存于服务账户可读的私有请求文件，服务消费后删除，不进入报告或数据库。

定向模式最多等待 32 分钟，只有发现新的 TrendRadar 采集进程才提交一次请求。等待期间密钥只在 SSH 进程内存中，资源每五秒采样；触发后请求文件有效期 15 秒，finevent 每秒检查该私有文件，成功预留后立即配合模型采集一轮。正常采集频率不变。触发后约每 100 毫秒观测资源和采集进程，继续观察到该轮结束（模型完成后最多再等两分钟），同时检查报告生成日志和 HTTPS。采集进程只导出 PID 和启动标识，不导出参数或环境变量。

模型与配合采集的毫秒时间记录保存在新增的 `ai_probe_timings` 表，不改写旧账本或新闻表；兼容先前验证版。只有完整采样区间位于两项任务的公共区间，且当时存在实际 TrendRadar 采集进程，才标记三者重叠。超时、资源不足或未取得共同观测时刻时保留未完成状态，不自动追加付费调用。未指定 `-OverlapAfter` 的常规探测仍使用每秒资源采样，并执行下面的完整演练流程。

随后演练实际回滚与恢复新版，会短暂中断 finevent。演练前停止写入并保存最新数据库，使用独立的演练备份目录，保留这两天的样本及模型费用记录；恢复后校验数据库摘要和完整性、HTTPS 入口与备份定时器。TrendRadar 保持运行。该流程与内存调整试验共用互斥锁，发现新的压力时保存报告并停止后续步骤。

报告下载到 `.validation/verification-*.json`，最新路径见 `.validation/verification-latest.json`；原始数据库备份仅留在服务器 `/srv/example/finevent-backups/verification-*`。报告状态 `completed_for_review` 只表示流程结束，需根据分时段采集、失败原因、资源、AI 状态及回滚结果进行验收。异常时保留证据并检查服务状态，避免直接重复执行付费探测。

如果 SSH 或下载中断，运行 `.\scripts\verify-server.ps1 -Report` 只取回上次报告，不部署或调用模型；交互验收收到连接中断信号时会保存当时进度，并不保证后台继续完成。不要用重新运行完整命令代替取回报告。

若在历史检查阶段因内存压力停止，先运行只读诊断（使用对应报告 ID）：

```powershell
.\scripts\inspect-memory.ps1 -VerificationId verification-…
```

诊断复用已有 `before.db` 快照，统计低内存样本的时间和频率，并读取当前进程 RSS、容器内存限制、cgroup 计数及保留的 OOM 日志。只下载筛选后的 JSON 报告到 `.validation/memory-*.json`；不部署、不调用模型，也不调整服务或内存阈值。历史低内存的连续采样跨度不等于持续时长，当前进程占用不能反推历史占用；日志未保留时 OOM 时间仍需标为未知。

2026-10-28 用户已授权在现机执行内存调整试验：

```powershell
.\scripts\repair-memory.ps1
```

脚本核对已诊断的容器身份、原 160 MiB 限额、无现有 Swap、ext4/xfs 磁盘及至少 3 GiB 剩余空间，等待 TrendRadar 空闲且主机余量至少 128 MiB 后开始。备份 `/etc/fstab` 和原容器限额，在 `/srv/example/finevent-host/swapfile` 完整分配 1 GiB 文件，并加入开机启用项；试用 TrendRadar 192 MiB 内存、最多 256 MiB Swap。Docker 的 `memory-swap` 参数是内存与 Swap 合计，因此设置为 448 MiB，并核对实际 cgroup 限额。[Docker 参数说明](https://docs.docker.com/engine/containers/resource_constraints/#--memory-swap-details)。Swap 提供缓冲，也可能增加耗时；finevent 的 96 MiB 和禁用 Swap 配置保持不变。

随后每五秒采样，覆盖接下来的两个正常半小时采集周期，约 35–65 分钟；保持终端开启，等待最终报告下载，不手动叠加额外采集。复核 OOM 增量、内存和 Swap 峰值、网页报告生成标记及两个 HTTPS 入口。报告及回退命令保存在 `/srv/example/finevent-backups/memory-recovery-*`，报告下载到 `.validation/memory-recovery-*/report.json`，入口见 `.validation/memory-recovery-latest.json`。`completed_for_review` 仍需审核，不能代替长期共存或 AI 负载验收。

准备失败时尝试恢复原配置；观察期中断或发现新故障时保存报告并保留试验配置，避免在内存紧张时自动关闭 Swap。按报告中的命令回退时，脚本会核对文件身份、配置是否被另行修改，以及物理内存和原容器限额能否容纳回收页面；不足时保留缓冲并报告未完成。勿直接重复 apply 或手工删除已启用的 Swap 文件。容器重建后的配置持久化仍待试验通过后完成，主要 TODO 位于 [调整脚本](deploy/memory_recovery.py) 的 `docker update` 调用旁。

首轮调整已完成两轮采集且无新增 OOM，但主机最低余量为 55.68 MiB，尚未通过 64 MiB 检查线。后续在相同硬限额内试用 160 MiB 的提前回收边界：

```powershell
.\scripts\test-memory-reclaim.ps1
```

该命令要求上一轮报告已结束、原 Swap 已启用且当前容器仍为 192 MiB RAM / 256 MiB Swap；只给该容器的 systemd scope 设置运行时 `MemoryHigh=160M`，不修改全机 `vm.swappiness`。超过此边界会触发回收和节流，不能保证占用始终低于边界，可能降低采集速度，机制见[内核 memory.high 文档](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#memory-interface-files)。观察两个正常采集周期，同时检查内存压力：连续三个采样间隔中，所有任务因内存而停顿的时间比例均达到 50% 时，先撤销该规则再收集诊断；新增 OOM、观察异常或结果未达标也会尝试撤销这条规则，保留前一轮 RAM/Swap 配置。硬断电等无法执行清理的情况需按报告回退命令处理。

该规则成功后也仅保留为待审核的运行时策略，持久化仍按调整脚本中的 TODO 处理。报告位于 `.validation/memory-high-*/report.json`，路径记录于 `.validation/memory-high-latest.json`。脚本结束后还有一次 SCP 下载，可能再次提示服务器密码；看到 `Report downloaded` 才表示报告已取回。

2026-10-28 首次提前回收试验因内存停顿自动撤销，报告还确认主机 `vm.swappiness=0`，期间未使用 Swap。该值会强烈推迟换出，并非禁用 Swap；[内核说明](https://www.kernel.org/doc/html/latest/admin-guide/sysctl/vm.html#swappiness)列出的默认值为 60。下一轮针对这一因素，显式启用运行时策略试验：

```powershell
.\scripts\test-memory-reclaim.ps1 -EnableSwapPolicy
```

此选项要求当前值仍为 0，先保存原值，再设置主机 `vm.swappiness=60` 和容器 `MemoryHigh=160M`。这是整机换页偏好，可能影响其他允许使用 Swap 的进程；脚本同时监测整机与容器 full PSI。观察失败或收到可处理的中断时，先撤销提前回收边界，再恢复原换页偏好；独立发生的配置修改不被覆盖。保留已启用的 Swap 和 RAM/Swap 硬限额，不清空 Swap，也不写入 `/etc/sysctl*` 开机配置。通过后仅保留运行时值待审核；持久化待办见 [试验脚本](deploy/memory_high_trial.py) 中的 TODO。报告路径和下载方式与上一命令一致。

2026-10-29 起优先评估精简 TrendRadar 和原生部署。先取回上一轮结果，并进行只读清点：

```powershell
.\scripts\inspect-trendradar.ps1
```

脚本读取容器身份、挂载与端口、安装源码版本和导入位置、允许导出的配置字段与环境变量覆盖、进程 RSS/PSS 及所属 cgroup、启动内存保留线索，以及 Nginx 中 TrendRadar 的路由。配置解析只临时运行现有 Python 的 YAML 解析器及包版本元数据读取，不导入 TrendRadar 或 LiteLLM，不启动采集。只在上一轮试验已释放锁、主机余量至少 128 MiB、容器占用低于 64 MiB 且距下一正常采集至少一分钟时读取配置；未满足条件则明确跳过对应项。原网页、数据、服务和配置均保持现状。

只下载筛选后的报告，不导出密钥、完整配置、进程参数或环境变量。报告位于 `.validation/trendradar-audit-*/report.json`，入口为 `.validation/trendradar-audit-latest.json`；同一命令还将最新内存试验报告取回到其对应目录的 `report-audit-download.json`。每次 SCP 都可能要求重新输入服务器密码，终端不录制。此报告用于制定精简与原生部署的对照测试，静态导入链本身不代表依赖的实际内存成本。

只读报告已确认 6.11.0 的 AI/翻译/推送/RSS 已关闭，仍存在启动时导入 LiteLLM 的路径。下一步在现有固定镜像下测量延迟导入候选：

```powershell
.\scripts\measure-trendradar-imports.ps1
```

该命令依次运行基线、原版、候选、候选、原版五个临时容器，只导入 `trendradar.__main__`，不执行主流程。原版与候选均核对审计得到的客户端文件摘要，候选仅在临时解释器内把 `completion` 的导入移至 `AIClient.chat`；安装源码不变。容器无网络、无生产配置/数据挂载、非 root、只读根目录，限制 192 MiB 内存、禁用 Swap，每次最长 45 秒，结束后清理自己创建的容器及其匿名卷。正常 Docker 服务和 TrendRadar 容器保持运行。

每次启动前要求主机余量至少 256 MiB、生产容器占用低于 64 MiB、距已知半小时采集时间至少四分钟；测量中余量不足 96 MiB、生产开始采集或 OOM 计数变化时终止探测并尝试清理。强杀或整机断电可能来不及清理，报告保留临时容器名称，需核对所有权标签后处理。建议避开整点和半点附近运行；不足时保留报告，不放宽阈值重试。

报告位于 `.validation/import-probe-*/report.json`，入口为 `.validation/import-probe-latest.json`。比较启动峰值 RSS、耗时、实际加载的模块和容器峰值；两种导入方式各测两次。离线导入会影响网络初始化行为，结果不能替代真实采集、HTML 正确性或原生部署验收；下一步需用隔离数据验证完整流程，才考虑生产采用。

启动对照已通过后，执行固定输入的流程回归：

```powershell
.\scripts\measure-trendradar-imports.ps1 -Workflow
```

此选项在原版、候选两个临时容器中分别执行两轮采集处理，沿用上述隔离、限额、45 秒超时及清理保护。核对实际 6.11.0 源码摘要，使用独立配置、固定时间和九个来源的测试响应；覆盖新增、重复、标题修订、无效标题及一个来源失败重试。实际运行原有采集解析、SQLite 写入、关键词处理和 HTML 生成；比较数据库结构、全部业务表内容、finevent 读取字段及四份 HTML 的摘要。只替换 HTTP 返回值与测试时钟，不访问实时新闻或生产输出。

报告位于 `.validation/workflow-probe-*/report.json`，入口为 `.validation/workflow-probe-latest.json`。输出一致仅证明这些固定输入下的行为一致；仍需在采用候选后验证实际配置、正常定时采集、网页入口、内存峰值和回退。本机固定版源码预演不能代替服务器同镜像回归。

服务器固定输入回归已通过，开始一次生产采集试用：

```powershell
.\scripts\test-trendradar-lazy.ps1
```

避开整点、半点前四分钟；开始时沿用至少 256 MiB 主机余量及生产容器空闲的检查。脚本先备份原客户端文件、当天新闻 SQLite 和当前 HTML，再把 `completion` 导入移入 `AIClient.chat`。修改后的语法树须与已经验证的候选一致，保留原文件的其他代码、注释、属主和权限；原子替换前再次核对摘要。源文件被独立修改、镜像/容器身份变化或上一试验仍运行时不覆盖。

数据库读取使用审计确认的主机绑定挂载 `/srv/example/trendradar/output`，先核对 Docker 挂载和容器内文件身份，避免解析 `/proc/<pid>/root` 时丢失容器挂载视图。若上一轮在改动前发生 SQLite 打开错误，新一轮先只读比较旧路径与主机路径；无法复现并解释旧错误时，保存诊断并停止。报告保留失败阶段、SQLite 错误码和代码位置，不导出原始日志、异常消息或配置内容。

试用保留现有九源、配置、网页、Docker、内存和 Swap 策略，不额外启动实时采集。观察在临时 systemd 服务中执行，SSH 断开后继续，机制见 [systemd-run 文档](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)。约 35–65 分钟覆盖两次正常采集，终端下载的首次报告可能仍为 `observing`，`Planned end` 是北京时间的预计结束时间。到时取回结果：

```powershell
.\scripts\test-trendradar-lazy.ps1 -Report
```

`-Report` 只读取已有试验的结果和观察服务状态，不重跑修改。验收检查 OOM 增量、可用内存、压力停顿、两次 HTML 日志，以及实际 SQLite 的采集/来源状态、当前 HTML 更新时间、finevent 的导入成功及来源时间。报告导出各来源失败数量供审查，不把整体入库成功当成九源都健康。报告在 `.validation/trendradar-lazy-*/report.json`，入口为 `.validation/trendradar-lazy-latest.json`。

观察异常或检查不通过时尝试恢复原客户端，保留已产生的数据和 HTML；不自动用旧数据库覆盖新采集。成功后保留候选待审核。回退命令和源文件备份在服务器同名备份目录；观察仍运行时，停止报告记录的 systemd unit 会触发回退处理。进程被强杀或主机掉电可能无法执行回退，`-Report` 会提示观察服务已停止而报告未完成，需依据状态及备份核查。该补丁目前仅存在于当前容器可写层，重建/更新容器会丢失；通过后再处理可重复部署，主要 TODO 位于 [试用脚本](deploy/trendradar_lazy_trial.py)。

用户已确认不再维护 handy-stock，并授权停用其 API。执行：

```powershell
.\scripts\stop-handy-stock.ps1
```

脚本只操作 `handy-stock-api.service`，核对运行程序与 unit 身份，记录启用状态及恢复命令，然后停止服务并移除其自启设置；应用目录、数据和 unit 文件保留。存在其他激活/停用关联时先留下报告，避免连带停用其他服务。完成后确认进程已退出，并检查 finevent 和 TrendRadar 网页。该操作不调整 Nginx，handy-stock API 自身将不可用；若稍后检查失败，也不会自动重启这个已弃用的应用。

报告保存到 `.validation/retire-handy-*/report.json`，入口为 `.validation/retire-handy-latest.json`。报告只包含指定状态字段、内存快照和恢复命令，不导出 unit 正文或环境变量。停用前后的主机内存还受其他任务影响，不能直接当作停用收益。

## UI 辅助开发

按 [AGENTS.md](AGENTS.md) 使用仓库内 [interface-design](.agents/skills/interface-design/SKILL.md)，先读取 [DESIGN.md](DESIGN.md)。skill 来自 vue-baseline 固定的上游提交 `2f9be3206855bcb2d1d0af262c8bae25cba6658d`，保留 MIT 许可证；仅第三方 skill 原文不参加 Oxfmt。业务设计记录在 DESIGN.md，数值和行为引用源码。

## 提交与合并

提交前完成上述对应检查并审查 diff。`pnpm hooks:install` 安装 Lefthook。钩子会修复并重新暂存匹配文件，需检查副作用。最后按 Git 暂存版本校验变动的知识笔记，发现本机或聊天标识时阻止提交。提交信息采用 Conventional Commits。正式审查前将零散修补整理为可构建的逻辑提交，每个提交包含必要测试；托管协作采用 PR 和 rebase merge，平台配置与验证见下方 [CI](#ci)。

## CI

[GitHub Actions](.github/workflows/ci.yml) 在 PR 创建、更新、重开及编辑，`main` 推送和手动触发时执行冻结安装、格式、Lint、前端与知识工具单测、Go 测试及 vet、部署工具回归和 Chromium 冒烟。浏览器入口包含类型检查及构建，随后复用前端产物编译 Linux amd64 程序、打包并验证本地渲染。PR 还会检查事件 base/head 之间全部非 merge 提交的信息，不检查 PR 标题。

所有必要步骤（包括通用包构建、渲染回归及上传）成功后才通过 PR 的 `Quality` 汇总检查；主分支及手动运行显示 `CI result`。通用包以 `finevent-linux-amd64-<检出SHA>` 命名，作为 Actions artifact 保留 14 天；失败浏览器报告保留 7 天。只上传 `release/generic/` 的压缩包与摘要，本地渲染包和 `.validation/` 不上传。Actions 固定提交 SHA，Dependabot 每周检查其更新；过期运行自动取消。Lint warning 沿用基线默认行为，不额外阻断。

`main` 已配置分支保护：通过 PR 合入，要求分支与主分支同步、GitHub Actions 提供的 `Quality` 成功；管理员同样受约束，禁止强推及删除，仓库仅允许 rebase merge。个人仓库不额外要求他人审批。平台配置与仓库重建记录见 [实施状态](docs/implementation-status.md#github-仓库重建)；本地检查不能替代 PR 最新提交的远端结果。

## 项目知识

项目接入 Basic Memory，知识 Markdown 随 Git 管理，运行时及数据库保存在本机缓存。安装、worktree 绑定及 hooks 验证见 [项目知识接入](docs/project-knowledge.md)。先安装 uv，再运行 `pnpm knowledge:setup` 和 `pnpm knowledge:doctor`；普通构建与 CI 不依赖知识运行时。

开发时使用项目技能按需检索和维护；`pnpm knowledge:check` 检查受变更影响的笔记，`pnpm knowledge:find 关键词` 提供本地字面检索回退。不要复制其他项目的数据库或绑定配置。

## 维护与验收

每季度复查依赖支持状态、已知风险及配置例外，安全公告或兼容性问题出现时及时评估。依赖按实际需要升级，同步版本声明与锁文件，执行完整 CI 和受影响行为的成功、失败路径验证；记录升级理由与结果。

配置例外在相关注释或 PR 中记录范围、原因、责任人和复查条件；依赖或规则升级时确认保留、收窄或移除。工程配置参考 vue-baseline 的 `b8564eb`，保留当前 workspace、Go/PWA 集成及既有导航和状态实现。

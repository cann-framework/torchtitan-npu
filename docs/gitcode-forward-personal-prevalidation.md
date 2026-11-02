# 个人 GitCode / 组织 GitHub 控制预验证

本阶段不是 CANN GitCode 官方 PR 自动门禁验收。用户授权在个人 GitCode
`depeng1994/torchtitan-npu` master 和组织 GitHub
`cann-framework/torchtitan-npu` master 发布同一控制对象后验证。

## 三源集成

- 个人 GitCode 基线 `69a822c0efabb3ce5b8e965c760824291ab5c672`：保留已实测的无 Secret、无源码执行上下文探针。
- GitHub 组织基线 `f20d073aec6bb7543b2e7db706d1e01b5298c8ca`：保留当前原生 schema-3 prepare/wait/common、Smoke/Nightly 默认选择及 result jobs。
- 已审 Forward 源 `1c2df02f262aff261bda580385ced72c8082ce14`：保留独立 schema-4 GitCode Prepare/Waiter jobs、固定 Check IDs、可信 case 选择及所有来源门禁。

合并历史保留三者祖先关系，最终同一提交可正常快进两个目标，不 force。
组织已经移除的三个旧 schema-2 业务入口不重新启用；历史
`gitcode-forward-upstream-integration.md` 描述的是之前仅对齐 GitCode 的候选，
不覆盖此处组织当前 schema-3 接口。原生 jobs 与 Forward jobs 用
`source_provider` 分流，native result 不为 GitCode dispatch 产生假失败或假成功。

## 预验证配置边界

实际 GitCode 目标和预验证 U 均为个人仓 ID `9075802`，GitHub C 仓 ID
`1210038933`，双 master 指向同一发布对象。现有 Checks App `5575236`，
installation `177722908`；不创建个人 GitHub 仓/App。发布后仍须单独核验
workflow ID、SDK pin、Runner Bundle digest、可信配置与服务接入，不能由本文件
冒充已部署。首轮选择 `worker_os_blue` 与 `dispatch_smoke_8p`，不加载权重。

实测 GitCode manual context 的 event_name 是 `Manual`；CLI 接受该明确别名，
同时检查 native `ATOMGIT_EVENT_NAME`，任何 MR/未知事件均不能因 custom 变量
缺失或被填 manual 而退化成人工周期观察。当前 CLI 不消费原始 Action run ID，
仍是共享持久状态的显式本机观察工具，不是已完成的自动 Action adapter。
`ATOMGIT_RUN_ID` 的真实存在不能弥补缺失的 workflow_sha 或原始事件时序证明。
Bridge YAML 仍在非注册模板目录，不启用自动 MR 权限路径。

## 实际验证

三源合并后针对性 CPU 回归 134 项通过（不代表完整产品 UT/NPU/端到端）。
两步探针每步环境变量 20/7，总计固定 42 字段；真实 manual run 曾完成，
个人 PR 原始来源证据仍需独立观察。权重训练、硬件执行、正式 CANN GitCode
PR 门禁均不因该 CPU 回归或 manual 结果自动视为通过。

## Hosted Manual capture 开发入口

`.gitcode/workflows/torchtitan_forward_manual.yml` 是独立的个人仓手动预验证入口，
只接受 `workflow_dispatch`，且运行上下文必须为实测的 `Manual`、个人仓 master、
attempt 1。它不是原始 PR 事件适配器；不能用其成功结果授权合入当前 PR。
每次只选 `worker_os_blue`（无 NPU）或 `dispatch_smoke_8p`（无权重、75 秒任务预算）。
平台调度、排队及准备时间不包含在 75 秒中，也不保证总时长。

两个 Hosted jobs 不共享文件系统：

1. `manual_capture` 的唯一 step `Capture manual request` 使用平台
   `ATOMGIT_TOKEN` 读取同仓 PR 当前快照及控制版本，输出一行限长、规范化的请求。
   不 checkout、不执行 PR 源码，不输出凭据。生成器固定完整 inline script；
   Worker 从真实 job API 独立核对该脚本 hash、step 状态、日志和时间边界。
2. `manual_forward` 独立读取前一 job 的完整日志，两次核验一致后派发组织 GitHub
   workflow `381287884`。GitHub GET 使用公开只读访问，只有固定 dispatch POST
   使用 `LITE_ACTIONS_GITHUB_TOKEN`。响应不确定时只按 nonce 查找既有 run，不重发 POST。
   等待实际 Prepare、Waiter、result 三个固定 jobs 成功，否则失败关闭。

GitCode v8 的日志时间戳只允许按已实测格式剥离一次；任意前缀、重复请求及变更的
脚本均拒绝。`ATOMGIT_RUN_ID` 提供真实 run ID；空的 `workflow_sha` 不以 head SHA
冒充来源证明。这里的权威是实际 GitHub dispatch 操作者与固定控制代码，GitCode
手动 capture 只是一次当前观察，不证明原始 PR 事件顺序。

成功输出明确区分历史执行与当前权限：

```json
{
  "historical_execution_result": "PASS",
  "current_authority": "NOT_PROVIDED",
  "not_current_merge_authorization": true,
  "merge_gate": "NOT_PROVIDED"
}
```

最终 H/B 重读相等不能排除已发生的 A→B→A 撤销。因此不将 GitCode job 绿色解释为
当前 PR 门禁通过。正式 CANN 自动路径仍需新鲜、绑定具体代际的 authority proof。
现有 GitHub Waiter 与 Blue 的来源、固定 Check 和代际校验不因此放宽。

### 启用前置条件（需逐项实测）

- 审核后把同一新控制提交发布至个人 GitCode master 和组织 GitHub master，更新
  `LITE_GITCODE_BRIDGE_SHA`、可信配置的控制 pin 和 Runner Bundle digest。
- 部署支持 `gitcode_manual_capture`、state 6 的已审 Worker SDK，GitHub Hosted SDK
  pin 同步；Blue 的 `manual_capture` 配置绑定真实注册 workflow ID、路径、job
  `manual_capture`、step identifier `1`、上述 step name、完整脚本 SHA、shell `bash`、
  working directory `.` 及 `request_authority=github_dispatch_operator`。
- 确认个人 Actions 启用；注册后的 workflow ID 不从文件名猜测。
- 明确授权配置 `LITE_ACTIONS_GITHUB_TOKEN`（仅目标组织仓 Actions write、Metadata
  read，短期有效），不使用 Checks App 私钥作为 dispatch 凭据。
- 实测自动 `ATOMGIT_TOKEN` 能读取同仓 PR、run、jobs 和日志。工作流声明仓库/PR/项目
  read 及 `action: read`（仅读流水线）权限；若平台拒绝则停止并确认最小权限，不能暗中上传本地 PAT。
- GitHub 公开 API 的匿名读取受平台速率限制；限流/权限不足会失败关闭，不自动扩大
  token 权限。受保护 NPU 资源仍由 Blue lease/quarantine 控制，不在 Hosted job 使用。

这些是部署检查项，不表示已经配置或完成真实端到端验收。

### 修改与 CPU 验证

通用 manual capture、forward/receipt 校验、三 case 展示和 aggregate gate 已归入
Lite Actions SDK 的 `ci_core.gitcode_manual`。模型仓只保留六个 Job 配置和一个约 4 KB
的固定 SDK bootstrap。Bootstrap 通过完整 SDK commit SHA 下载公开源码归档，校验归档
SHA-256，仅提取 `ci_core/`，并检查归档路径、文件类型及解压大小；不使用 PAT 下载 SDK，
不引入服务或新授权协议。

从仓库根目录重新生成受审 workflow：

```bash
python3 -B .gitcode/scripts/build_hosted_manual_workflow.py
```

生成器输出 `capture_script_sha256`，必须与 Worker 可信配置的
`manual_capture.script_sha256` 一致。SDK SHA 和 archive SHA 必须固定为完整值；禁止
`latest`、分支名或未校验的动态下载。`tests/unit_tests/tooling/gitcode_hosted/test_manual_sdk_bootstrap.py`
覆盖六 Job、凭据隔离、OS-only 默认、显式三 case、bootstrap 体积和真实隔离 subprocess
下载/校验/执行 SDK aggregate。SDK 仓的 `tests/test_gitcode_manual_forward.py` 保留原
capture/dispatch/receipt/canonical/generation/nonce/cleanup/aggregate 安全回归；不代表
真实 NPU 或完整产品 UT。

### 流水线读取权限与失败定位

自动平台 token 的流水线读取域为 singular `action: read`，与 repository/project 的读取
域不同，参见[官方 Token 权限表](https://docs.gitcode.com/docs/help/home/org_project/pipeline/security-permissions/token-permissions/)。
这不是 `action: write`，不授予运行其他流水线或修改配置权限；仍须实际运行确认 v8 API 能力。

`API.request` 的 HTTP 错误只输出固定服务/端点类别和状态码，例如
`API_GITCODE_ACTION_RUN_HTTP_403`。不输出请求 URL、ID、响应体、header 或 token。
首个 API 为同仓当前 run GET；该读取失败不说明 GitHub dispatch token 权限不足。
若修改 common/capture 脚本，须重新生成工作流，并同步可信 `script_sha256` 与 U=C pin，
不能把诊断格式更新视为无需版本核验的热补丁。

### 平台步骤包络

GitCode v8 job detail 的 `steps` 包含平台步骤，不能直接把数组长度等于 1 当作
“只有一个用户步骤”。当前验证器除原单用户步骤形态外，仅接受实测的固定三步：
`official_pre_step`（初始化步骤、sequence 0）、唯一固定 `official_shell`
（sequence 1）、`official_post_step`（后处理步骤、sequence 2）。
平台步骤必须同 job、已完成、ID 唯一；identifier、inputs、env、condition 必须显式
为 null；缺字段、空列表、额外 script/command 字段、重复或重排步骤均拒绝。
用户脚本 hash、shell、工作目录、完整 job/log 双读及年龄校验不放宽，捕获时间必须
取实际用户步骤而非初始化步骤。去秘密真实 API fixture 和负例位于
`tests/unit_tests/tooling/gitcode_hosted/fixtures/gitcode_b472_platform_steps.json`。

### Hosted source 失败诊断与跨平台追踪

Hosted runtime 对 `SourceError` 只输出已审固定枚举，例如
`{"phase":"prepare","stage":"SOURCE","code":"UNSUPPORTED_SOURCE_FILE_MODE"}`。
非白名单内容、多参数异常及其他异常不回显原文，标为 `UNCLASSIFIED`；不得输出 Git
命令 stderr、请求、凭据或环境。安全诊断不等于已经修复来源构建失败，也不放宽来源校验。

GitCode forward 在独立核验实际 GitHub run 的仓库、操作者、控制 SHA、工作流及 nonce
之后立即输出 `GITHUB_RUN_BOUND`，包含该 case、数字 run ID 和固定仓库 run URL。
即使随后 Prepare/Waiter 失败，该链接也保留用于排查；该事件不是 PASS，不授予当前 PR
合入权限。未知 dispatch 仍只查既有 nonce，不自动重发 POST。

### 首次 run title 初始化窗口

真实运行曾在首 GET 报告 `DISPLAY_TITLE` 值不符，随后同一 run 与 capture nonce 精确
匹配；首 GET 的标题原文未保存，因此“平台初始化不同步”仍是推断，不是已证初始值。
仅本次 dispatch 成功响应返回、固定 API/HTML URL 核验过的数字 run ID，允许一次有限
初始化窗口：其余五字段、仓库、操作者、路径及实际响应 ID 必须精确匹配；标题须为
不含控制字符、不以 `lite-gitcode:` 开头的限长字符串，且 run 仍 queued/in_progress。
最多六次 GET、30 秒，同时不超过原请求 deadline 和 capture 300 秒年龄；不刷新预算。
缺字段、类型错误、另一 nonce 或其他身份冲突立即拒绝，不等待错误身份变成正确。

窗口内只输出固定 `INITIAL_TITLE_PENDING` 与次数，不输出标题原文，不重新 POST、
不换 run ID 或 nonce。首次标题精确匹配后永久回到严格校验，后续任何改变仍拒绝。
这不暂停已经启动的 GitHub Prepare；Blue 自身的 nonce、固定请求与准入校验保持独立，
不能把 GitCode 等待状态解释成新执行锁或当前 PR 合入授权。

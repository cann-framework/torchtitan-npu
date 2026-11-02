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

从仓库根目录重新生成受审脚本和 workflow：

```bash
python3 -B .gitcode/scripts/build_hosted_manual_workflow.py
```

生成器输出 `capture_script_sha256`，必须与 Worker 可信配置一致。生成文件不得绕过
源脚本手工修改。`tests/unit_tests/tooling/gitcode_hosted/test_hosted_manual.py`
使用合成 API 覆盖脚本/日志绑定、真实 SDK 编码契约、权限边界、单次派发恢复、超时及
历史结果语义；不代表真实平台、NPU 或完整产品 UT。无需导入 torch 或初始化 NPU。

### 流水线读取权限与失败定位

自动平台 token 的流水线读取域为 singular `action: read`，与 repository/project 的读取
域不同，参见[官方 Token 权限表](https://docs.gitcode.com/docs/help/home/org_project/pipeline/security-permissions/token-permissions/)。
这不是 `action: write`，不授予运行其他流水线或修改配置权限；仍须实际运行确认 v8 API 能力。

`API.request` 的 HTTP 错误只输出固定服务/端点类别和状态码，例如
`API_GITCODE_ACTION_RUN_HTTP_403`。不输出请求 URL、ID、响应体、header 或 token。
首个 API 为同仓当前 run GET；该读取失败不说明 GitHub dispatch token 权限不足。
若修改 common/capture 脚本，须重新生成工作流，并同步可信 `script_sha256` 与 U=C pin，
不能把诊断格式更新视为无需版本核验的热补丁。

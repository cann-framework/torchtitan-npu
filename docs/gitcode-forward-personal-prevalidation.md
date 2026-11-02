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

## Agent skills

### Issue tracker

Local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout at repo root. See `docs/agents/domain.md`.


## MCP + mattpocock Skills 集成映射

所有 `mattpocock-skills:*` skill 在执行时，必须优先使用 codebase-memory-mcp 工具进行代码探索，而非依赖逐文件 grep/read。具体映射如下：

### diagnosing-bugs
- Phase 1（构建反馈循环）之前：先 `search_graph` + `trace_path` 定位与 bug 相关的模块、调用链和数据流
- Phase 2（复现+最小化）：用 `trace_path` 确认最小化后的调用关系
- Phase 3（假设）：用 `query_graph` 分析高复杂度/高扇出模块作为候选假设
- 替代硬编码的 `CONTEXT.md` 读取：直接用 `get_architecture` 获取模块结构

### code-review
- 分析 diff 时：优先调用 `detect_changes()` 获取结构化的变更影响图（含命名函数/路由/调用链）
- 审查调用链安全时：用 `trace_path(risk_labels=true)` 评估变更的传播风险
- 标准检查中使用 `search_code` 查找项目内约定模式

### tdd
- 红-绿-重构循环前：用 `search_graph` 查找已有函数、类型、接口签名
- 编写测试时：用 `trace_path` 理解被测函数的调用链依赖
- 重构阶段：用 `query_graph` 分析扇入/扇出发现提取候选

### prototype
- 快速原型前：用 `get_architecture` + `search_graph` 理解相关模块边界和接口
- 避免重复造轮：用 `search_code` 查找已有工具函数

### research
- 代码探索阶段：优先使用 `search_graph` + `trace_path` 代替 grep
- 结果记录时：将 MCP 查询到的结构关系一并写入 research 文档

### domain-modeling
- 理解领域边界时：用 `get_architecture(clusters=true)` 查看实际模块聚类
- 分析依赖时：用 `query_graph` 查询跨模块调用关系

### codebase-design
- 分析耦合度时：用 `query_graph` 查询高扇入/扇出节点
- 寻找深化机会时：用 `get_architecture` 查看 Leiden 聚类结果

### resolving-merge-conflicts
- 理解冲突上下文时：用 `trace_path` 追踪冲突代码的调用者/被调用者
- 用 `detect_changes` 了解冲突区域的变更历史

### improve-codebase-architecture
- Phase 1（探索）中：用 `get_architecture(clusters=true)` 替代人工漫游，直接获取 Leiden 聚类视图
- 寻找深化候选时：用 `query_graph` 查询高扇入/扇出、高复杂度模块
- 热点分析时：用 `detect_changes(depth=2)` 识别最近高频变更区域
- 用 `search_graph(max_degree=0, exclude_entry_points=true)` 识别死代码/孤立模块

### triage
- 验证 bug 时：用 `trace_path` 追踪从入口到出错的完整调用链
- 检查冗余时：用 `search_graph` + `search_code` 判断请求的功能是否已存在
- 评估 PR diff 时：用 `detect_changes` 理解变更波及范围

### implement
- 执行 `/tdd` 前：自动用 `search_graph` 理解代码库现有结构和约定
- 按 seam 分割实现时：用 `trace_path` 验证模块边界
- 实现中引用已有代码时：用 `search_graph(name_pattern=...)` 精确查找

### to-spec
- Step 1（探索代码库）中：用 `get_architecture` 获取项目结构概览，用 `search_graph` 查找关键模块
- 描述测试 seam 时：用 `trace_path` 理解当前测试边界

### to-tickets
- Step 2（探索代码库）中：用 `search_graph` + `get_architecture` 理解模块边界来设计垂直切片
- 识别预重构机会时：用 `query_graph` 分析扇入/扇出判断提取候选

### wayfinder
- research ticket 执行时：用 `search_graph` + `trace_path` 加速代码探索
- 评估 ticket 可行性时：用 `get_architecture` 理解项目整体结构

### ask-matt
- 当用户描述场景时：用 `search_graph` + `search_code` 快速理解当前项目状态，以便推荐最合适的 skill

### grill-with-docs / grilling
- 在深度访谈中涉及代码假设时：用 `trace_path` 或 `search_graph` 验证用户的直觉，用事实替代纯提问1

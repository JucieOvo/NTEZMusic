# NTEmusic 发布工作区同步方案

> 作者：JucieOvo
> 日期：2026-07-11

---

## 一、目标

将当前开发工作区 `F:\NTEZMusic\` 的关键产物同步到发布工作区 `F:\NTEZMusic\rel\`，形成两个分支：

| 分支 | 路径 | 用途 | 受众 |
|------|------|------|------|
| 研究分支 | `F:\NTEZMusic\` | 完整开发环境，含所有基准产物、原始音频、研究文档 | 开发者 |
| 工作分支 | `F:\NTEZMusic\rel\` | 开箱即用工具链，精简体积，不含可再生的中间产物 | 用户 |

---

## 二、执行步骤

### 步骤 1：rel 现状保护（Git 存档）

```powershell
cd F:\NTEZMusic\rel
git add -A
git commit -m "chore: 发布工作区同步前存档 (2026-07-11)"

# 记录提交哈希
git log --oneline -1
```

**目的**：确保旧版本可回溯。

### 步骤 2：清理 rel 旧文件

删除以下目录的旧版本（已通过 git 存档）：

```
删除 rel\src\            → 完整替换为新版
删除 rel\config\         → 完整替换为新版
删除 rel\docs\           → 完整替换为新版（精选）
删除 rel\tests\          → 完整替换为新版
删除 rel\work\           → 仅保留 SoundFont/tools，其余清理
删除 rel\requirements.txt
删除 rel\README.md
删除 rel\AGENTS.md
删除 rel\adaptive_octave_fold_algorithm.md  → 已移至 docs/
删除 rel\usage_guide.md                     → 已移至 docs/
```

### 步骤 3：同步到 rel

#### 3.1 必须同步（覆盖）

| 源路径 | 目标路径 | 原因 |
|--------|----------|------|
| `src\` | `rel\src\` | 核心工具链代码 |
| `config\arrangement_benchmark_20260711.yaml` | `rel\config\` | 双歌曲基准范例配置 |
| `config\` 中其他核心配置 | `rel\config\` | YAML 曲谱示例 |
| `tests\` | `rel\tests\` | 测试套件 |
| `requirements.txt` | `rel\requirements.txt` | 依赖声明 |
| `README.md` | `rel\README.md` | 项目说明 |
| `AGENTS.md` | `rel\AGENTS.md` | 项目指令 |
| `pyrightconfig.json` | `rel\pyrightconfig.json` | 类型检查配置 |
| `.gitignore` | `rel\.gitignore` | 版本忽略规则 |

#### 3.2 精选同步（文档）

| 源文档 | 同步 | 理由 |
|--------|:--:|------|
| `adaptive_octave_fold_algorithm.md` | Y | 默认推荐算法文档 |
| `attention_weighted_algorithm.md` | Y | 排行榜第二名 |
| `attention_weighted_rerank_algorithm.md` | Y | 精排算法 |
| `hands_decoupled_algorithm_report.md` | Y | 排行榜第三名 |
| `hands_decoupled_v2_final.md` | Y | v2 升级文档 |
| `audio_to_yaml_converter使用说明.md` | Y | 用户使用指南 |
| `audio_to_yaml_pipeline方案.md` | Y | 管线设计 |
| `audio_pipeline_svsep_mpdr_developer_guide.md` | Y | SVSEP-MPDR 开发者指南 |
| `project_code_overview.md` | Y | 项目代码概览 |
| `yaml_score_guide.md` | Y | YAML 曲谱格式说明 |
| `usage_guide.md` (root) | Y | 快速使用指南 |
| `piano_auto_play方案.md` | Y | 自动弹奏原理 |
| `PLAN.md`, `DESIGN.md`, `PRACTICE.md`, `AUDIT.md` | Y | 项目核心设计文档 |
| `proposal_role_decoupled_dlaf.md` | N | 研究性提案 |
| `score_aware_theory_arrangement_plan.md` | N | 已淘汰模式文档 |
| `score_optimization_discussion.md` | N | 内部讨论 |
| `mp3_to_midi_*` | N | 转录基准，研究材料 |
| `upagain_*` | N | UpAgain 项目方案 |
| `sustain_split_design.md` | N | 内部研究 |
| `svsep_integration_proposal.md` | N | 内部提案 |
| `MusicTheory/` | N | 乐理参考 |
| `rel_workspace_sync_plan.md` | N | 本方案文档 |
| `superpowers/` | N | 开发工具链 |
| `score-audit-svsep-mpdr-v3/` | N | 内部审计 |

#### 3.3 精选同步（work/ — 不可再生资产）

| 源路径 | 同步 | 理由 |
|--------|:--:|------|
| `work\arrangement_benchmark_20260711\soundfont\` | Y | Salamander Grand Piano SoundFont（~1GB），渲染必需 |
| `work\arrangement_benchmark_20260711\tools\` | N | fluidsynth/ffmpeg 应通过系统包管理器安装 |
| `work\arrangement_all_modes_20260711\` | N | 五歌曲基准产物，可重新生成 |
| `work\arrangement_benchmark_20260711\` 中的基准产物 | N | 双歌曲 WAV/MP3 可重新生成 |
| 根目录 `*.mp3` / `*.flac` | N | 原始音频，研究素材，体积过大 |
| `piano_svsep\` | N | GNN 模型权重，通过 modelscope 下载 |

#### 3.4 必须排除

| 路径 | 原因 |
|------|------|
| `.claude\` | 开发工具缓存 |
| `.pytest_cache\` | 测试缓存 |
| `.sisyphus\` | 开发工具配置 |
| `.superpowers\` | 开发工具数据 |
| `ANo\` | 未知用途 |
| `UpAgain\` | 独立项目 |
| `list_mumu_windows.py` | 开发调试脚本 |
| `play_upagain.bat` | 开发调试脚本 |

### 步骤 4：Git 提交与验证

```powershell
cd F:\NTEZMusic\rel
git add -A
git status  # 检查变更范围
git commit -m "feat(rel): 同步开发工作区至发布分支 (2026-07-11)

- 更新 src/ 全量工具链代码
- 更新 tests/ 测试套件
- 更新 config/ 配置与曲谱示例
- 精选同步核心文档至 docs/
- 更新 requirements.txt / README.md / AGENTS.md
- 排除可再生的基准产物和研究性文档"
```

### 步骤 5：验证清单

- [ ] `rel\src\` 目录结构完整
- [ ] `rel\tests\` 可运行（`cd rel && pytest -q`）
- [ ] `rel\config\` 包含范例 YAML
- [ ] `rel\docs\` 包含使用指南和算法文档
- [ ] `rel\work\arrangement_benchmark_20260711\soundfont\` SoundFont 完整
- [ ] `rel\requirements.txt` 依赖可安装
- [ ] Git 提交历史可追溯

---

## 三、预期体积

| 目录 | 预估大小 | 说明 |
|------|---------|------|
| `src\` | ~5 MB | Python 源码 |
| `tests\` | ~1 MB | 测试代码 |
| `config\` | <1 MB | YAML 配置 |
| `docs\` | ~3 MB | Markdown 文档 |
| `work\soundfont\` | ~1 GB | 钢琴音源（必需） |
| **合计** | **~1.1 GB** | 排除 git 历史后约 1 GB |

---

> 作者：JucieOvo

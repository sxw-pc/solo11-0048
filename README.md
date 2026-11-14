# 采样抖动核验台（jitterlens）

面向**本地离线时间序列采集日志**的采样间隔抖动诊断与重采样契约核验工具。

`jitterlens` 读取本地 CSV/JSONL 采样记录，依据声明式 YAML 采样契约，检查时间戳间隔是否满足标称周期、允许抖动、允许丢点率、单调性与时钟漂移要求；定位抖动、丢点、回拨、重复、乱序所在的时间段与来源，输出可复现的 JSON / Markdown / HTML 报告、SQLite 台账和独立的重采样计划。它**只做核验和计划，不修改原始数据**。

## 定位与边界

**它做：**

- 完全离线、可一条命令跑通的采样合规诊断。
- YAML 契约驱动：每个数据源声明标称周期、绝对/相对抖动、gap 上限、丢点比例、时间戳单位与时区、单调性、重复策略、漂移窗口等。
- 读取 CSV 与 JSONL，支持 ms / us / s 的 epoch 时间戳和 ISO-8601 时间戳，支持按文件名和 `source` 标签路由到契约数据源。
- 检测结构异常（回拨、乱序、重复）和间隔异常（jitter、gap、clock_drift），聚合成时间区间并按来源汇总。
- 输出确定性 JSON、纯本地 Markdown 与内联样式 HTML，追加式 SQLite 台账。
- 生成确定性的补齐/降采样**计划**，写入单独输出目录；不写回输入日志。
- 内置 fixture 生成器，断网可复现完整流程。

**它不做：**

- 不采集数据、不联网、不推送、不访问远程服务。
- 不是监控平台、时序数据库、可视化大屏或告警系统。
- 不做预测、机器学习异常检测、插值补写。
- 不做多租户、鉴权、RBAC、调度器。
- 不处理 CSV/JSONL 之外的输入格式，不做数据脱敏、血缘或实体归并。
- 不做曲线交互页面；HTML 报告仅为静态单文件。

## 快速上手

要求 Python 3.10+。运行期仅使用标准库；PyYAML 是可选的，缺失时自动使用内置极简 YAML 子集解析器。契约解析走语义闸门：两种解析路径结果必须一致，超集语法（如 `0100`、`yes`、锚点、flow mapping）在两类环境中同样被拒绝。测试只依赖 `pytest`。

```sh
make test
make demo
```

`make demo` 等价于：

```sh
python -m jitterlens demo
```

它会依次：

1. 在 `fixtures/generated/` 生成 3 个合成数据源；
2. 用 `contracts/sensors_default.yaml` 分析；
3. 在 `build/demo/` 写入 `report.json`、`report.md`、`report.html`、`plans.json` 和 `ledger.sqlite`；
4. 打印摘要。内置默认数据应返回 `warn`，退出码为 `0`。

也可以直接分析自有目录：

```sh
python -m jitterlens run \
  --contract contracts/sensors_default.yaml \
  --input-dir fixtures/generated \
  --out-dir build/run \
  --html
```

只读查询台账：

```sh
python -m jitterlens query runs --db build/demo/ledger.sqlite
python -m jitterlens --query intervals --db build/demo/ledger.sqlite --limit 10
```

可选表单：`runs`、`sources`、`intervals`、`parse_errors`、`plans`。

## 契约示例

```yaml
version: 1
generated_at: "2025-01-01T00:00:00Z"

defaults:
  period_ms: 1000
  jitter_abs_ms: 25
  jitter_rel: 0.02
  max_gap_points: 3
  max_gap_ratio: 0.05
  monotonic: true
  duplicate_policy: mark
  merge_gap_points: 2
  drift_window: 64
  drift_threshold_rel: 0.007
  histogram_buckets: [5, 25, 50, 100, 250, 1000]
  timestamp:
    column: ts
    unit: ms
    format: epoch
    tz: UTC

sources:
  - name: mock-sensor-a
    match:
      filename: "mock-sensor-a.csv"
      value: mock-sensor-a
    period_ms: 1000
```

完整字段、错误码、判定公式与版本兼容策略见 `docs/CONTRACT.md`。架构与确定性保证见 `docs/DESIGN.md`。

## 输入约定

- CSV：必须有契约声明的 timestamp 列；可选 `source` 列用于多源路由。空行、字段数不匹配、非法时间戳会记录为 parse error，不会中断整次运行。
- JSONL：每行一个 JSON 对象，至少含 timestamp 字段；可选 `source` 字段。非法 JSON、非对象行、缺失时间戳会记录为 parse error。
- 支持目录递归扫描，文件按相对路径稳定排序；同一文件可以由 `source` 列路由给多个契约源。
- 所有输入按只读打开。测试会在运行前后对 fixture 做全量 sha256 比对。

## 输出

- `report.json`：机器可读报告，含 `run_id`、契约 sha256、输入清单 sha256、来源统计、异常区间、parse error、规则命中计数和计划摘要。
- `report.md`：人可读报告，无外链、无远程资源。
- `report.html`：可选静态报告，内联 CSS，无脚本、外链、远程字体或图片。
- `plans.json`：补齐/降采样计划。补齐按 `period_ms` 推算，受 `max_gap_points` 截断并标记 `truncated`；降采样保留确定性代表点。计划只写入输出目录。
- `ledger.sqlite`：追加式台账，含 `runs`、`sources`、`intervals`、`parse_errors`、`plans` 五张表。重复运行不会覆盖历史记录。

JSON 报告使用排序键、稳定列表顺序和固定小数位输出。相同输入与契约下，剔除 `generated_at` 与 `run_id` 后，两次运行的 JSON 字节一致。

### 报告样例

`report.json` 的核心结构（示例只保留关键字段）：

```json
{
  "schema_version": 1,
  "run_id": "jl-b255581824ed01cd",
  "generated_at": "2025-01-01T00:00:00Z",
  "contract": {"name": "sensors_default.yaml", "version": 1, "sha256": "..."},
  "inputs": {"manifest_sha256": "..."},
  "summary": {
    "verdict": "warn",
    "sources": 3,
    "valid_points": 581,
    "parse_errors": 3,
    "abnormal_intervals": 7,
    "rule_hits": {"on_time": 549, "jitter": 27, "gap": 6, "clock_drift": 129},
    "plans": {"fill": 3, "downsample": 1, "truncated": 2}
  },
  "sources": [
    {
      "name": "mock-sensor-a",
      "verdict": "warn",
      "counts": {"valid_points": 240, "parse_errors": 2},
      "gap_ratio": 0.020408,
      "jitter_ratio": 0.008368,
      "histogram": [{"le_ms": 5, "count": 222}]
    }
  ],
  "intervals": [
    {
      "id": "mock-sensor-a#0002",
      "source": "mock-sensor-a",
      "start_ts": 1735689719004,
      "end_ts": 1735689725001,
      "kinds": ["gap"],
      "point_count": 2
    }
  ]
}
```

`plans.json` 中的补齐计划示例：

```json
{
  "id": "plan-0002",
  "kind": "fill",
  "source": "mock-sensor-a",
  "start_ts": 1735689719004,
  "end_ts": 1735689725001,
  "expected_missing": 5,
  "insert_ts": [1735689720004, 1735689721004, 1735689722004],
  "truncated": true
}
```

Markdown 报告按“来源分级表、时序统计、抖动直方图、Top 异常区间、parse error、计划摘要”组织。

## 退出码

| 场景 | 退出码 |
| --- | --- |
| 全局 verdict 为 `ok` 或 `warn` | 0 |
| 全局 verdict 为 `fail` | 2 |
| 使用 `--no-fail-exit` | 0（即使 fail） |
| 契约、输入目录、台账等运行错误 | 1 |
| 参数错误 | argparse 约定（通常 2） |

`make demo` 中默认契约对内置 fixture 的预期 verdict 是 `warn`，因此命令成功返回 0。`contracts/sensors_strict.yaml` 对同一批 fixture 预期返回 `fail`，用于演示退出码 2。

## 离线与合规声明

- 运行期不发起任何网络请求；报告不引用 CDN、远程字体、远程图片或远程脚本。
- 业务时间一律取自数据或契约中的 `generated_at`，不读取系统时钟。
- 仅处理本人所有或已获授权的数据。本仓库 fixture、契约与来源标签均为合成名称，不含密钥、真实公司名、真实域名或真实目标地址。
- 项目不包含采集、远程推送、云同步、脱敏、鉴权或调度能力。

## 目录

```text
jitterlens/          应用代码（sources / core / report）
contracts/           默认与严格采样契约
fixtures/generated/  fixture 生成目录（默认由命令生成）
fixtures/golden/     默认契约的 golden 报告
scripts/             无 CLI 依赖的 fixture 生成入口
tests/               离线测试（含确定性、只读性、离线性）
docs/                设计与契约说明
```

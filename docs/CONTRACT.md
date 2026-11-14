# 采样契约与判定规则（CONTRACT）

本文档定义 `jitterlens` 契约 YAML 的字段、读取与路由规则、错误码、分类公式、统计口径、计划规则和版本兼容策略。

## 1. 文档结构

```yaml
version: 1
generated_at: "2025-01-01T00:00:00Z"
defaults:
  period_ms: 1000
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

- `version`：必需，当前只接受 `1`。
- `generated_at`：报告使用的业务时间字符串，必需或使用固定占位值；不读取系统时钟。
- `defaults`：所有 source 的默认字段；source 中的同名键覆盖默认值。
- `sources`：非空列表；每个元素是一个数据源声明。
- 未知键当前被忽略以便向前兼容；未知 `version` 不会静默接受。

### YAML 子集

未安装 PyYAML 时使用内置 `yamlmini`，仅支持：

- 块映射、块列表（`- item`）；
- 行内标量列表（如 `[5, 25, 50]`）；
- 单/双引号字符串、整数、浮点、布尔、null；
- `#` 注释。

不支持锚点、别名、tag、块标量、多行字符串、flow mapping 和复杂 YAML。解析错误返回 `E_CTR_001` 并定位到行号。若环境存在 PyYAML，则自动使用 `yaml.safe_load`，内置解析器与其对本仓库契约的结果一致（有测试覆盖）。

## 2. source 字段

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `name` | string | 无，必需 | 稳定的来源标识，报告与计划使用它 |
| `match.filename` | string | `*` | 文件名 glob；同时匹配相对路径和 basename |
| `match.value` | string / null | null | 匹配 `source` 标签值；null 表示接受唯一候选 |
| `match.column` | string | `source` | source 标签字段的说明字段，保留用于扩展 |
| `source_column` | string | `source` | CSV 的 source 标签列名 |
| `source_field` | string | `source` | JSONL 的 source 标签字段名 |
| `timestamp.column` | string | `ts` | 时间戳列 / 字段名 |
| `timestamp.unit` | `ms` / `us` / `s` | `ms` | epoch 时间戳单位；ISO 格式时仍校验但不参与换算 |
| `timestamp.format` | `epoch` / `iso8601` | `epoch` | 时间戳格式 |
| `timestamp.tz` | string | `UTC` | 当前只接受 UTC；ISO 中的显式偏移会被归一化 |
| `period_ms` | int > 0 | 1000 | 标称采样周期 |
| `jitter_abs_ms` | int >= 0 | 25 | 绝对抖动容差 |
| `jitter_rel` | number >= 0 | 0.02 | 相对周期抖动容差 |
| `max_gap_points` | int >= 0 | 3 | gap 阈值倍数；也是补齐计划候选上限 |
| `max_gap_ratio` | number in [0,1] | 0.05 | 丢点比例 fail 阈值 |
| `fail_jitter_ratio` | number in [0,1] | 0.25 | jitter 比例 fail 阈值 |
| `max_parse_error_ratio` | number in [0,1] | 0.05 | parse error 比例 fail 阈值 |
| `monotonic` | bool | true | 是否要求输入流单调不回拨 |
| `duplicate_policy` | `mark` / `allow` | `mark` | 是否把重复时间戳标记为异常 |
| `merge_gap_points` | int >= 0 | 2 | 异常点聚合成区间时允许跨过的正常点数 |
| `drift_window` | int >= 4 | 64 | 漂移窗口的间隔数 |
| `drift_threshold_rel` | number >= 0 | 0.01 | 漂移窗口累计相对变化阈值 |
| `downsample_threshold` | number >= 0 | 0.5 | 触发降采样计划的相对偏差阈值 |
| `histogram_buckets` | 正整数递增列表 | `[5,25,50,100,250,1000]` | 绝对偏差直方图桶上界 |

字段类型或取值范围不合法时：

- 缺失 / 范围不合法：`E_CTR_002`；
- 类型错误：`E_CTR_003`；
- 重复 source 名：`E_CTR_004`；
- 不支持的版本：`E_CTR_005`。

## 3. 读取与路由

### 文件扫描

- 递归扫描 `--input-dir` 下的 `.csv` 和 `.jsonl`，其他后缀忽略。
- 文件以相对 POSIX 路径排序。
- 有扩展名但没有 source 契约匹配时，产生 `E_READ_004`（source 为 `<unmatched>`），不中断其他文件。

### 路由

1. 先按 `match.filename` 找出候选 source。
2. 从记录中读取 source 标签（CSV 的 `source_column`、JSONL 的 `source_field`）。
3. 优先匹配 `match.value == label`，其次匹配 `name == label`，再次接受唯一一个无 `match.value` 的候选。
4. 无标签时只有唯一候选才能路由；有多个候选且缺 source 标签时产生 `E_READ_004`。

### CSV

- 第一行必须是表头；空表头、重复表头导致 `E_READ_004`。
- 空行、字段数与表头不一致导致 `E_READ_002`；非法时间戳导致 `E_READ_003`。
- 每行未知列名保留在内部点记录中，报告只累计计数。

### JSONL

- 每行必须是 JSON 对象；空行、非法 JSON、非对象导致 `E_READ_002`。
- 缺失时间戳或非法时间戳导致 `E_READ_003`。
- 未知键保留在内部点记录中，报告只累计计数。

### 时间戳

- epoch 支持整数或十进制文本；ISO-8601 支持 `Z` 和显式偏移；无时区按 UTC。
- 所有格式先解析为精确有理数毫秒（`Decimal`/`Fraction`，无浮点），再由统一算子收口：精确值必须落在 `[0, 2**63 - 1]` ms 内（负值与越界一律拒绝，报 `E_READ_003`），然后按 ROUND_HALF_UP 落成整数毫秒。
- 等价表述（`s`/`ms`/`us`/ISO-8601）必然得到同一个整毫秒；亚毫秒舍入会在该 source 报告的 `rounding` 块中留下 `{origin, lineno, raw, exact_ms, ts_ms, direction}` 事件。

## 4. 结构异常

按每个 source 的输入顺序：

- `out_of_order`：当前时间戳小于此前输入的最大时间戳。
- `monotonic`：在 `out_of_order` 条件成立且 `monotonic: true` 时同时标记。
- 按稳定排序后，同一时间戳的后续出现标记 `duplicate`（`duplicate_policy: mark` 时）。
- 所有诊断都带 `origin` 与 `lineno`。

排序键为：

```text
(ts_ms, origin, lineno, input_seq)
```

## 5. 间隔分类

令 `delta = current.ts_ms - previous.ts_ms`，`dev = |delta - period_ms|`。

判定优先级：

1. `delta == 0` -> `duplicate`（不参与 on_time/jitter/gap 计数）。
2. `delta > max_gap_points * period_ms` -> `gap`。
3. `dev <= jitter_abs_ms` 或 `dev / period_ms <= jitter_rel` -> `on_time`（两个条件是“或”，边界包含）。
4. 其他 -> `jitter`。

`jitter` 与 `gap` 会同时标记相邻两个点，便于异常区间覆盖事件发生的时间段。短间隔也可能被判为 jitter。

### 时钟漂移

- 取非重复相邻间隔，窗口大小 `drift_window`，步长 `max(1, drift_window // 2)`。
- 每个窗口取整数 lower median。
- 连续至少 3 个窗口的中位数严格同向单调；
- 首尾中位数相对标称周期的变化 `> drift_threshold_rel * period_ms`；
- 将这些窗口覆盖的点标记为 `clock_drift` 并记录漂移区间。

漂移是附加标签，不替换原有 jitter/gap 分类。

## 6. 区间聚合

- 收集所有至少带一个异常 kind 的点；
- 相邻异常点之间允许间隔 `merge_gap_points` 个正常点；
- 合并后：
  - `kinds` 为并集并按字典序排序；
  - `point_count` 为组内异常点数；
  - `start_ts` / `end_ts` 为组内首尾异常点时间戳；
  - 记录首尾来源与行号；
- 区间按 `(source, start_ts, end_ts, start_lineno)` 排序，ID 为 `<source>#NNNN`；
- 同时计算每个 source 的最长连续异常点长度（不做 merge 放宽）。

## 7. 统计口径

每个 source 输出：

- `records = valid_points + parse_errors`；
- `unique_points = 去重后的时间戳数量`；
- `timing`：全部相邻 delta（含 duplicate 产生的 0）的计数、均值、中位数、最大/最小值；
- `histogram`：非重复相邻间隔的绝对偏差；每个桶条件为 `dev <= upper`，最后一项 `le_ms: null` 是 overflow；
- `jitter_ratio = jitter 相邻间隔数 / 非重复相邻间隔数`；
- `missing_estimate = sum(ceil(delta / period) - 1)`，只对 `gap` 分类的相邻间隔计算；
- `gap_ratio = missing_estimate / (unique_points + missing_estimate)`；
- `parse_error_ratio = parse_errors / records`；
- `rule_hits`：
  - `on_time`：分类为 on_time 的相邻间隔数；
  - `jitter` / `gap` / `clock_drift` / `duplicate` / `monotonic` / `out_of_order`：带该 kind 的点数；
  - `parse_error`：parse error 条数；
- `unknown_field_count`：未知字段/列的出现次数。

### verdict

**fail** 条件（任一）：

- `monotonic` 点数 > 0；
- `gap_ratio > max_gap_ratio`；
- `parse_errors > 0` 且 `parse_error_ratio > max_parse_error_ratio`；
- `jitter_ratio > fail_jitter_ratio`；
- 无记录或无有效点。

**warn** 条件：

- 存在任意异常点或 parse error / duplicate / clock drift；
- 没有触发 fail。

**ok**：无异常、无 parse error。

全局 verdict 取所有 source 的最高级别；未路由输入至少使全局为 warn。

## 8. 计划

计划位于输出目录的 `plans.json`，不修改输入。

### 补齐（fill）

- 对含 `gap` kind 的区间生成；
- 从区间 `start_ts + period_ms` 开始，按周期生成小于 `end_ts` 的时间戳；
- 最多生成 `max_gap_points` 个；
- `expected_missing` 为理论上应插入的候选数；
- 若生成数小于理论值，`truncated: true`。

### 降采样（downsample）

- 对含 `jitter` 的区间，若其中存在相对偏差 `> downsample_threshold` 的 jitter 相邻间隔则生成；
- 区间内按排序顺序每 2 个点保留 1 个，`keep_every: 2`；
- 保留集合写为 `keep_ts`，不写回原始日志。

计划 ID 在排序完成后编号为 `plan-NNNN`。计划不执行插值、不对齐、不写回。

## 9. 错误码

| 代码 | 含义 |
| --- | --- |
| `E_CTR_001` | 契约 YAML 无法读取或解析（带可能行号） |
| `E_CTR_002` | 契约字段缺失或取值越界 |
| `E_CTR_003` | 契约字段类型错误 |
| `E_CTR_004` | 契约 source 名称重复 |
| `E_CTR_005` | 不支持的契约版本 |
| `E_READ_001` | 输入目录不存在、无支持文件或文件不可读 |
| `E_READ_002` | 空行、坏行、字段数不匹配、非法 JSON |
| `E_READ_003` | 缺失或非法时间戳 |
| `E_READ_004` | 无法路由、表头问题、缺失时间戳列/字段 |
| `E_LEDGER_001` | 台账不存在、无法打开或查询失败 |
| `E_LEDGER_002` | 查询了未知台账表 |

parse error 会进入报告与台账，不会导致整批分析崩溃。CLI 级错误返回退出码 1。

## 10. 版本兼容策略

当前 loader 只接受 `version: 1`。

v1 → v2 迁移原则：

1. **绝不静默改变 v1 语义**。若新增字段需要不同判定，必须提升版本号。
2. v2 可以新增可选字段；v1 内容在 v2 schema 下必须显式声明迁移后的默认值。
3. 读取 v2 契约的 loader 必须同时接受 v1；读取器遇到更高版本返回 `E_CTR_005`，不猜测字段。
4. 报告保留 `schema_version` 与契约版本；golden 升级必须伴随迁移说明。
5. 新增数据源格式应通过 reader 接口扩展，不改变现有 `SamplePoint` / issue 契约。
6. 新增漂移模型应保持 `clock_drift` kind 与区间字段稳定，避免破坏下游消费。

## 11. 兼容性边界

- 报告中的时间戳为整数毫秒，不写系统时钟。
- 排序与 JSON 序列化规则属于输出契约；修改它们需要更新 golden。
- 内置 YAML 子集是有意限制；复杂契约可安装 PyYAML 使用完整 safe_load，但本仓库样例必须同时通过两种解析路径。

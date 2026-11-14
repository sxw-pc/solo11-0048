# 设计说明（DESIGN）

## 1. 目标

`jitterlens` 是一个纯本地的采样节律核验引擎。它把“采集日志时间戳 + 采样契约”转换为：

1. 结构诊断（回拨、乱序、重复）；
2. 间隔诊断（on_time / jitter / gap / clock_drift）；
3. 异常区间与按来源汇总；
4. 可复现报告与追加台账；
5. 只读的补齐/降采样建议计划。

运行期不联网，不读取系统时钟作为业务输入，不修改输入文件。

## 2. 处理流水线

```text
契约 YAML
  -> config.py（加载、合并 defaults、schema 校验）
  -> sources（目录扫描、CSV/JSONL 解析、source 路由、时间戳归一）
  -> core.timeline（分组、稳定排序、结构异常）
  -> core.classify（相邻间隔分类、漂移窗口）
  -> core.intervals（异常点聚类）
  -> core.stats（统计、直方图、分级）
  -> core.plan（补齐/降采样计划）
  -> report（JSON / Markdown / HTML）
  -> ledger（SQLite 追加）
```

### 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py` | 契约解析、默认值合并、字段校验、source 匹配 |
| `routing.py` | 统一的归属判定：候选计算、优先级、判定理由（config 与读取器共用） |
| `yamlmini.py` | 内置 YAML 子集解析器，支持带行号的稳定错误 |
| `sources/__init__.py` | 路由、时间戳归一、目录扫描、读取结果聚合 |
| `sources/csv_reader.py` | CSV 表头、坏行、空行、时间戳与标签处理 |
| `sources/jsonl_reader.py` | JSONL 对象校验、未知字段计数、标签处理 |
| `core/timeline.py` | 按源分组、`(ts, origin, lineno)` 稳定排序、回拨/乱序/重复标记 |
| `core/classify.py` | 相邻点间隔分类、绝对/相对容差、漂移窗口 |
| `core/intervals.py` | 异常点聚类为区间，稳定 ID 与排序 |
| `core/stats.py` | 时序统计、直方图、规则命中、verdict |
| `core/plan.py` | 从异常区间生成确定性计划 |
| `report/*` | 三种本地化报告 |
| `ledger.py` | SQLite schema、追加写入、只读查询 |
| `engine.py` | 端到端编排、输入清单哈希、run_id、输出落盘 |
| `cli.py` | 子命令、退出码、错误呈现 |
| `fixturegen.py` | 确定性合成数据，不依赖随机数或系统时钟 |

## 3. 确定性保证

同一份契约与输入，在相同选项下必须产生确定的报告体。

1. **输入清单**：递归扫描 `.csv` / `.jsonl`，按相对路径的 POSIX 形式排序；对每个文件计算 sha256、长度，再按
   `path\0sha256\0size\n` 拼接后计算 manifest sha256。
2. **source 顺序**：报告与计划中的 source 按名称排序。
3. **点排序**：按 `(ts_ms, origin, lineno, input_seq)` 排序。时间戳相同也保持可解释的稳定顺序。
4. **区间排序**：按 `(source, start_ts, end_ts, start_lineno)`；区间 ID 在排序后编号。
5. **JSON 序列化**：`json.dumps(..., sort_keys=True, indent=2, ensure_ascii=False)`，固定末尾换行，写入时指定 LF。
6. **契约时间**：报告使用显式 `generated_at` / `--generated-at`；不存在时使用固定占位值，不调用系统时钟。
7. **run_id**：由 schema 版本、契约 sha256、输入 manifest sha256 计算，不包含输出路径或时间。
8. **浮点规避**：时间戳一律为整数毫秒；相对抖动、漂移阈值、降采样阈值内部使用 `Fraction`，比较采用交叉相乘，不使用二进制浮点边界判断。统计输出中的均值/比例是展示值，按固定 3 位或 6 位小数舍入。
9. **哈希**：契约 sha256 来自契约原始字节；输入 sha256 来自原始字节；报告不写绝对路径。

确定性测试覆盖：

- 两次 fixture 生成的字节一致；
- 两次分析的 JSON（剔除 `generated_at` / `run_id`）字节一致；
- plan 文档字节一致；
- golden 报告一致。

## 4. 时间戳与排序

- epoch 单位 `ms` / `us` / `s` 使用 `Decimal` 转换到整数毫秒；`us` 转毫秒时按 ROUND_HALF_UP。
- ISO-8601 使用 `datetime.fromisoformat`，支持 `Z` 与显式偏移；无时区时按契约 `UTC`。
- 内部点结构为 `SamplePoint(ts_ms, source, origin, lineno, input_seq, unknown_fields)`。
- 结构异常使用输入顺序判断；间隔异常使用排序后顺序判断。

## 5. 结构异常

- `monotonic`：契约要求 `monotonic: true` 时，输入流中出现 `ts` 小于此前最大 `ts` 的点。
- `out_of_order`：无论是否要求单调性，输入流中出现时间回退的点。
- `duplicate`：按稳定排序后，同一来源同一时间戳的后续出现；`duplicate_policy: allow` 时不标记。
- 三类异常都保留行号与来源文件，报告中以诊断条目呈现。

## 6. 间隔分类与漂移

对排序后相邻点计算 `delta_ms`：

1. `delta == 0`：`duplicate`。
2. `delta > max_gap_points * period_ms`：`gap`。
3. `|delta - period| <= jitter_abs_ms` 或 `|delta - period| / period <= jitter_rel`：`on_time`。
4. 其他：`jitter`。

漂移检测使用非重复相邻间隔的滑动窗口：

- 窗口长度 `drift_window`，步长 `max(1, drift_window // 2)`；
- 每个窗口取整数 lower median；
- 连续至少 3 个窗口的中位数严格同向单调，且首尾相对标称周期的变化超过 `drift_threshold_rel`；
- 将这些窗口覆盖的点标记为 `clock_drift`，并记录漂移区间。

区间聚合把异常点按 `merge_gap_points` 允许的正常点间隔合并，合并后 kinds 去重。

## 7. 统计与 verdict

- 直方图统计非重复相邻间隔的绝对偏差；桶边界为“less than or equal”，最后一行是 overflow。
- `missing_estimate` 只统计被判为 `gap` 的相邻间隔。
- `jitter_ratio` 为 jitter 相邻间隔数 / 非重复相邻间隔数。
- `gap_ratio` 为估算丢失点 /（唯一观测点 + 估算丢失点）。
- verdict 的 fail 条件：单调性违例、gap 比例超限、parse error 比例超限、jitter 比例超限、无有效点。
- 其他异常或 parse error 至少为 warn。
- 全局 verdict 取所有 source verdict 的最大级别；未路由文件的 parse error 至少使全局为 warn。

## 8. 计划

计划只读、只写输出目录：

- `gap` 区间按 `period_ms` 生成候选插入时间戳；候选数受 `max_gap_points` 截断，`truncated` 标记是否发生了截断。
- `jitter` 区间中若存在相对偏差超过 `downsample_threshold` 的相邻间隔，生成降采样意图；保留区间内按排序顺序的偶数下标点，规则固定。
- plan ID 在全部 source/区间排序完成后顺序编号。
- 不执行插值，不对原始文件做写回。

## 9. 离线与安全

- 不导入网络客户端库；不启动 socket；不解析远程资源。
- HTML 只包含内联 CSS，不写入脚本、图片或外链。
- 输入文件以只读模式打开；所有输出路径由 `--out-dir` 决定。
- 所有合成标签使用 `mock-*` 形式，不包含真实主体、域名或密钥。

## 10. 扩展方向

- 新增 reader：实现与现有 reader 相同的 `(points, issues)` 返回契约，并在目录扫描处注册后缀。
- 新增漂移模型：替换 `_detect_drift`，保持 `drift_ranges` 与 point kind 输出稳定。
- 新契约版本：先扩展 config 的 merge/validation，再在 `CONTRACT.md` 增加迁移规则；未知版本必须保持稳定错误码。
- 新增报告格式：只消费 report 字典，不重新读取输入，确保报告之间语义一致。

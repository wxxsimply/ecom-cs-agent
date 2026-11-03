# 脱敏工单数据集（合成）

> 200 条工单，覆盖 13 类意图、12 笔演示订单、46 条退款/售后、21 条转人工。
> **全部为合成数据，不含真实个人信息**；期望值由 `app/refunds.py` 与 `data/knowledge/*.md` 推导，并由脚本校验。

## 文件

| 文件 | 用途 |
| --- | --- |
| `tickets_v1.jsonl` | 机器可读，一行一条；评测/统计用 |
| `tickets_v1.md` | 人眼可读的表格版，面试时可直接翻 |
| `../../scripts/build_eval_tickets.py` | 生成器：改规则/订单后重新生成 |
| `../../scripts/validate_eval_tickets.py` | 校验器：金额、人工标记、政策引用、PII |
| `../../tests/test_eval_tickets.py` | pytest 入口：数据集与退款规则一致性 |

## 字段

| 字段 | 说明 |
| --- | --- |
| `id` | `T-001`…；稳定引用 |
| `split` | `dev`（150）/ `holdout`（50） |
| `source` | 固定 `synthetic`，禁止把真实工单标成 synthetic |
| `scenario_date` | 订单事实的快照日期；重新 seed 后需重算 |
| `channel` | `web` / `wechat` / `wecom` |
| `history` | 多轮上下文（本轮全部为空，留给后续扩展） |
| `message` | 客户原话 |
| `order_no` | 关联 `scripts/seed.py` 的订单号，可为空 |
| `facts` | 订单快照：状态、金额、签收天数、品类、可退性、地区 |
| `expected.intent` | 14 类意图之一（本集覆盖 13 类；`human_handoff` 是系统动作，未作为用户话语） |
| `expected.action` | `answer` / `query_order` / `query_logistics` / `refund_apply` / `refund_reject` / `exchange_apply` / `cancel_order` / `handoff` / `clarify` |
| `expected.amount` | 仅退款动作有值；驳回为 `0.0` |
| `expected.reason_code` | `quality` / `wrong_item` / `damaged` / `missing` / `no_reason` |
| `expected.requires_human` | 是否必须人工 |
| `expected.handoff_reason` | 转人工原因（`complaint` / `emergency` / `policy_missing` / `delivery_dispute` …） |
| `expected.policy_refs` | `shipping.md#3` 这类引用，`#N` 为文件内第 N 个二级标题 |
| `expected.notes` | 期望理由，便于人工复核 |

## 标签怎么来的

- 退款金额、是否可退、是否人工：复刻 `app/refunds.py`（15 天质量期、7 天无理由、500 元人工线、定制品不可无理由）。
- 运费与政策：`data/knowledge/shipping.md`、`returns.md`、`faq.md`。
- 订单事实：`scripts/seed.py` 的 12 笔订单；`scenario_date=2026-11-03`。
- 生成后必须跑 `python scripts/validate_eval_tickets.py`；金额与规则不一致会直接失败。

## 怎么用

```powershell
python scripts/build_eval_tickets.py      # 重新生成
python scripts/validate_eval_tickets.py   # 校验（应输出 OK）
python -m pytest -q tests/test_eval_tickets.py
```

建议指标：

| 指标 | 口径 |
| --- | --- |
| 意图准确率 | `expected.intent` 与预测一致 |
| 动作准确率 | `expected.action` 一致（比意图更接近业务结果） |
| 金额准确率 | 退款动作的金额完全一致；**错一单即失败** |
| 人工标记准确率 | `requires_human` 一致（漏转人工比多转更危险） |
| 政策引用命中 | `policy_refs` 至少命中一条且不引用不存在的条款 |
| 澄清/转人工覆盖率 | `clarify` / `handoff` 样本不被强行作答 |

## 切分与防泄漏

- `dev` 用于调 prompt/规则；`holdout` 只在最终报告用一次。
- 不要在 `holdout` 上调参；改完再看 holdout，等于自欺。
- 本集是**工程期望**，不是业务专家标注；面试时要说清这一点。

## 加真实工单（不提交）

1. 按 `research/03-材料请求与脱敏.md` 脱敏：删姓名/电话/地址/订单号后四位、日期平移。
2. 放 `data/eval/private/`（已加入 `.gitignore`），不要提交。
3. 用 `research/04-评分表与证据登记.md` 的表格记录商家评分。
4. 只把**聚合指标**和**脱敏后的典型片段**写进作品集。

## 局限

- 合成分布偏“规则清晰”，真实工单更多是半截话、错别字、情绪与多诉求混合。
- 订单快照固定在 2026-11-03；重新 seed 后签收天数变化，需重算标签。
- 200 条只够做方向性回归，不足以证明线上效果。

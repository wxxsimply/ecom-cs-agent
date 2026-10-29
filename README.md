# 电商 AI 智能客服 Agent（P0-Lite）

一个可本地运行的电商客服演示：FastAPI + LangGraph + SQLite + 本地 BM25 检索 + SSE 对话页。

## 快速启动

```powershell
python -m pip install -r requirements.txt
python -m scripts.seed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。演示账号为 `u_demo`，前端会自动带 `X-User-Id`。

也可以：

```powershell
docker compose up --build
# http://127.0.0.1:8080
```

## 已实现（P0-Lite）

- 9 类意图规则识别 + 订单号/退款原因槽位 + 指代改写。
- 本地混合检索：BM25 + 字符 n-gram 余弦 + RRF + 轻量 rerank；政策 Top8 / 商品 Top5。
- SSE 对话：`meta → citation → token → done`；退款/订单选择使用 `interrupt`，`/chat/resume` 继续。
- 订单/物流查询；退款资格、金额、确认令牌、退款申请；取消/未支付/转人工出口。
- SQLite 会话/消息/订单/退款/工单/反馈/中断/确认令牌；LangGraph `SqliteSaver` 支持同进程/重启恢复。
- 引用展示、点赞点踩、会话历史、订单选择卡、确认卡、只读指标 API。
- 自测：`python -m pytest -q`（11 个测试）。

## 当前降级/边界

- 本地无 MySQL/Milvus/BGE-M3/Langfuse，使用 SQLite + 本地 BM25/字符向量；生产替换为 Milvus/BGE-M3 适配器。
- 默认 `LLM_MODE=mock`，回答由检索/规则/模板生成；可在 P1 接 OpenAI-compatible 模型。
- P0-Lite 仅本地单实例，`X-User-Id` 不是安全边界；公网前必须完成 JWT/RBAC/多实例/PII 审计。
- 这是演示实现，不包含真实订单/支付/物流系统；金额规则以 `data/knowledge` 与 `app/refunds.py` 为准，上线前需业务签字。

## 目录

- `app/`：FastAPI、LangGraph、检索、退款、SQLite 模型。
- `web/`：原生 HTML/CSS/JS 对话页。
- `data/knowledge/`：运费/退换/商品 FAQ 样例。
- `tests/`：API/SSE/退款/检索单元与集成测试。
- `docs/`：需求、方案、索引；根目录 `CONTEXT.md` 为执行范围与验收基线。

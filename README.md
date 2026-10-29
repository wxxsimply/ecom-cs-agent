# 电商 AI 智能客服 Agent（P0-Lite）

一个可本地运行的电商客服演示：FastAPI + LangGraph + SQLite + 本地 BM25 检索 + SSE 对话页。

## 快速启动

```powershell
python -m pip install -r requirements.txt
python -m scripts.seed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。

## 自测

```powershell
python -m pytest -q
```

## 当前实现边界

- P0-Lite：单实例、本地 SQLite、Mock/规则 LLM、本地 BM25 + 字符 n-gram 检索。
- BGE-M3/Milvus/Langfuse 以适配器接口预留；本地无模型/无外部服务时使用降级实现，详见 `docs/02-方案设计.md`。
- 公网部署前必须完成 JWT/RBAC/多实例/PII 审计等 P1 项。

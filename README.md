# CXJ Blog AI

CXJ Blog 的知识库智能问答服务。使用 Python 3.14、FastAPI、LangGraph、DeepSeek、硅基流动 BAAI/bge-m3 和 PostgreSQL/pgvector。

## 已实现

- Markdown 分块，1024 维向量与中文关键词混合检索，回答附文章引用。
- DeepSeek 真实流式输出；引用编号与公开文章版本校验成功后确认回答。
- 最近三轮连续追问；历史只用于改写检索问题，不作为知识事实。
- 文章变更与索引队列同事务写入；首次迁移为已有文章排队，草稿、下架和删除文章不进入回答。
- worker 支持租约恢复、最多五次退避重试及新版本替换旧任务。
- 内部接口 Bearer 认证，运行账号与迁移管理员账号分离，服务不开放公网端口。

博客前后端负责身份、流式展示和额度：游客每天10次、登录账号20次、管理员不限每日次数，北京时间零点重置。失败或完成前断连退还预留次数。额度不由这个内部服务单独判断。

## 本地开发

复制 `.env.example` 为 `.env`，仅在本地填写模型密钥和数据库连接。需要 Python 3.14、PostgreSQL 和 pgvector 0.8.7。

```bash
python -m venv .venv
# 激活虚拟环境后执行
python -m pip install -r requirements.txt
python -m ruff check app scripts tests deploy
python -m pytest -q
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --loop app.loop:loop_factory --no-access-log
```

AI 表迁移：`python -m scripts.migrate`。迁移管理员凭证只用于一次性迁移，不能放入生产 API 容器。`scripts.provision` 可在部署时创建专用账号并迁移。旧迁移有校验和，禁止修改已应用 SQL。

本地测试种子：`python -m scripts.seed_local`，仅允许本机 cxj_blog 数据库，包含明确标记的公开/草稿/下架测试文章。测试数据不打包到生产镜像。`INDEX_WORKER_ENABLED=true` 启用内置队列消费，或关闭后单独运行 `python -m scripts.worker`。`scripts.sync_posts` 用于全量重新排队，会重新调用嵌入 API。

## 内部接口

除 `/healthz` 外均需 `Authorization: Bearer <AI_INTERNAL_TOKEN>`，只允许博客后端或运维从内部网络调用。

- `GET /internal/readyz`：配置和数据库就绪检查，不探测外部模型。
- `POST /internal/ask`：普通问答。
- `POST /internal/ask/stream`：SSE问答，status/heartbeat/delta/done/error事件。
- `GET /internal/index/jobs`：最近100个索引任务及汇总。
- `POST /internal/index/jobs/{post_id}/retry`：重新排队。
- `PUT /internal/documents`：手动版本索引。

生成中的分块是未校验草稿；只有 done 为完成结果。断流、模型异常或校验失败不确认回答。固定两个并发问答槽位，生成不会自动重试。

## 云效部署

流水线 cxj-blog-ai-prod：Linux/amd64，开启 Docker Daemon，Bash 执行 `bash ci/build.sh`。Docker verify 阶段执行离线测试，runtime 阶段输出非 root 镜像。制品为 release/ 下的 image.tar 和 release.json，包含提交号和镜像归档 SHA256；不包含密钥、测试种子或本地安装产物。

可信部署脚本需单独安装到服务器，由云效主机任务调用，不能直接执行下载制品中的 root 脚本。详细配置及当前状态见 [docs/FLOW.md](docs/FLOW.md)，架构见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 验证与边界

本地离线测试覆盖流式分块、断流、错误引用、历史长度与制品校验；真实模型、Java网关及浏览器开发代理流式链路已验证。Linux Docker 构建与云效首次发布仍需在流水线验收。生产 pgvector、环境文件、可信脚本和 Nginx 流式代理已由管理员安装，AI 服务尚未首次部署，已有线上文章尚未生成嵌入。

密钥、密码、令牌、`.env`、`.local` 和制品不得提交。生产运行容器禁止持有 DATABASE_ADMIN_URL。迁移后失败不会自动恢复数据库，备份恢复需协调数据库和应用版本。

## 提交记录

| 日期 | 变更 | 验证 |
| --- | --- | --- |
| 2026-10-14 | 初次提交RAG服务、真实流式问答、连续追问、事务索引队列与云效镜像制品部署 | 本地离线测试及真实流式链路通过；首次云效构建和生产索引待验收 |

# 云效与生产部署

## 构建任务

流水线名cxj-blog-ai-prod，GitHub chenxvjie/cxj-blog-ai，分支main。首次发布手动触发；同一流水线并发1，超出排队。构建环境Linux/amd64，启用Docker Daemon，Bash执行：

```bash
set -Eeuo pipefail
docker version
bash ci/build.sh
```

构建会使用Python3.14镜像、清华pip源和锁定依赖，先执行verify阶段再生成runtime镜像。云效任务不注入模型密钥。上传通用制品至cxj-blog-artifacts，名称ai_${PIPELINE_ID}，版本${BUILD_NUMBER}，打包路径release/，不包含打包路径目录。

## 主机部署任务

复用cxj-blog-prod主机组，root执行，单批1台，超时30分钟，下载路径/opt/cxj-blog/incoming/ai/package.tgz。部署前可添加人工确认。任务脚本：

```bash
set -Eeuo pipefail
bash /opt/cxj-blog/deploy/deploy-ai.sh /opt/cxj-blog/incoming/ai/package.tgz
```

部署脚本与ai-artifact.py必须先单独安装为root持有，脚本700、Python辅助文件600。它们不能从下载的制品中直接执行。脚本共享.deployment.lock，验证归档路径、摘要、镜像标签和提交标记，先备份数据库，再使用一次性管理员容器执行scripts.provision，最后只重建ai服务。

## 生产环境

当前线上PostgreSQL16.15 Alpine。pgvector0.8.7已编译进同一基础版本的自建镜像，通过docker-compose.postgres.vector.yml覆盖，不替换原数据目录。数据库仍由博客Flyway管理公开表，AI专用表单独迁移，历史SQL不可修改。

.env.ai：模型密钥、专用运行账号DATABASE_URL、内部token、公开站点URL和worker开关；.env.ai-migrate：仅DATABASE_ADMIN_URL。两者root:root且600，只有一次性迁移容器读取管理员文件。模板在deploy目录，实际文件不提交。专用角色cxj_blog_ai仅AI表DML和公开文章表读取，不使用超级用户。

AI加入现有internal网络，不映射宿主机8000端口。非root运行、只读根目录、可写/tmp、512MB内存和1CPU。服务器需安装新版前后端部署脚本和preflight，以保留PostgreSQL与AI覆盖文件。手动Compose操作也必须合并现有前后端覆盖文件、docker-compose.postgres.vector.yml及docker-compose.ai.flow.yml。

Nginx /api/已配置proxy_buffering off、proxy_cache off、proxy_read_timeout和proxy_send_timeout为150秒、gzip off。保持原认证头、Cookie和X-Real-IP转发。后端AI_BASE_URL=http://ai:8000；AI_TRUSTED_PROXIES需匹配实际入口代理IP，Nginx容器重建换IP后应检查，不应盲目扩大信任范围。

## 首次现有文章索引

002迁移为所有已有文章创建任务；worker只对PUBLISHED且未删除文章生成嵌入，其余排除。每批最多16个分块，向量1024维。索引状态READY/PENDING/RUNNING/FAILED/EXCLUDED，可从内部接口查看；失败最多五次退避重试。不要重复运行全量同步脚本来等待进度，否则会产生新的版本和额外模型调用。

首轮应核对所有公开文章都有当前版本READY，并确认FAILED为0、草稿/下架不可检索，再开启后端AI_ENABLED及构建VITE_AI_ENABLED=true的前端。AI发布不自动部署前后端，生产入口在联调完成前保持关闭。

## 状态与恢复

2026-10-14：流水线已由用户配置保存；服务器pgvector、环境文件、可信脚本和Nginx流式代理准备完成。AI服务尚未首次部署，线上嵌入尚未生成。代码推送不代表发布验收通过。

脚本保存数据库备份；若失败时AI迁移校验签名变化或未知，阻止自动退回旧镜像。不会自动还原数据库或删除持久化数据。服务故障可先关闭博客AI入口，不影响阅读。保留旧镜像和备份，并另行安排异机备份及保留周期。

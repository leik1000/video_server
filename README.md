# Video Server

独立的视频上传与管理服务。上传走 FastAPI 直连鉴权，下载建议走 Nginx 静态目录。

## 功能

- `POST /api/videos/upload` 上传视频文件
- `GET /api/videos/{id}` 查询视频元数据
- `DELETE /api/videos/{id}` 删除视频和元数据
- `POST /api/videos/cleanup` 按容量上限清理最旧视频
- `GET /api/health` 健康检查

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy config.example.json config.json
.venv\Scripts\uvicorn app:app --host 0.0.0.0 --port 7000
```

把 `config.json` 里的 `api_token` 和 `public_base_url` 改成真实值。

## Docker 启动

```bash
docker compose up -d --build
```

生产环境建议通过环境变量设置密钥，不要把真实 token 提交到仓库。

## 上传示例

```bash
curl -X POST "https://video.example.com/api/videos/upload" \
  -H "Authorization: Bearer replace-with-a-long-random-secret" \
  -F "file=@task_xxx.mp4" \
  -F "task_id=task_xxx" \
  -F "source=leo2api-node-1"
```

返回：

```json
{
  "id": "task_xxx",
  "filename": "task_xxx.mp4",
  "size": 12345678,
  "content_type": "video/mp4",
  "source": "leo2api-node-1",
  "url": "https://video.example.com/videos/task_xxx.mp4",
  "created_at": 1710000000
}
```

## 存储与清理

视频默认保存到：

```text
./data/videos
```

元数据默认保存到：

```text
./data/video_index.json
```

服务按存储容量清理，不按天数过期。默认配置：

```json
{
  "max_storage_gb": 100,
  "prune_storage_gb": 1,
  "cleanup_interval_seconds": 3600
}
```

清理逻辑：每小时检查一次 `storage_dir` 的实际文件总大小。如果超过 `max_storage_gb`，按创建时间最旧的视频优先删除，直到总大小低于上限并且本轮至少释放 `prune_storage_gb`。`POST /api/videos/cleanup` 可手动触发同样的清理逻辑。

## Nginx 下载配置

如果使用 Docker Compose，容器外目录是：

```text
D:\projects\video_server\data\videos
```

Linux 服务器上通常映射为类似：

```text
/opt/video_server/data/videos
```

Nginx 示例：

```nginx
server {
    server_name video.example.com;

    client_max_body_size 1024m;

    location /videos/ {
        alias /opt/video_server/data/videos/;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location /api/ {
        proxy_pass http://127.0.0.1:7000;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

# Video Server

独立的视频上传与管理服务。上传走 FastAPI 直连鉴权，下载建议走 Nginx 静态目录。

## 功能

- `POST /api/videos/upload` 上传视频文件
- `GET /api/videos/{id}` 查询视频元数据
- `DELETE /api/videos/{id}` 删除视频和元数据
- `POST /api/videos/cleanup` 清理过期视频
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
  -F "source=leo2api-node-1" \
  -F "ttl_days=30"
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
  "created_at": 1710000000,
  "expires_at": 1712592000
}
```

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

## leo2api 对接方式

视频生成服务器完成本地下载后，请向：

```text
POST https://video.example.com/api/videos/upload
Authorization: Bearer <api_token>
```

上传 `data/generated/{task_id}.mp4`，然后使用响应里的 `url` 作为最终 `video_url` 返回给用户。

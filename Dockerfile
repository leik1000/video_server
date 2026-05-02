FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.example.json ./

ENV VIDEO_SERVER_STORAGE_DIR=/data/videos
ENV VIDEO_SERVER_METADATA_FILE=/data/video_index.json

EXPOSE 7000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7000"]

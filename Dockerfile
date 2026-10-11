FROM python:3.11-slim

WORKDIR /app

# 依存関係を先にコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションファイルを個別にコピー
COPY main.py .
COPY keep_alive.py .

CMD ["python", "main.py"]

FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir paho-mqtt==2.1.0 requests==2.32.3 fastapi==0.115.6 \
    "uvicorn[standard]==0.32.1" matplotlib==3.9.2
COPY . .
ENV PYTHONUNBUFFERED=1 TZ=America/Bogota

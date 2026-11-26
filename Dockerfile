FROM python:3.13-slim
WORKDIR /opt
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY . chinese_bot/
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/opt
EXPOSE 8000
CMD ["uvicorn", "chinese_bot.app:app", "--host", "0.0.0.0", "--port", "8000"]

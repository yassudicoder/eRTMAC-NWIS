FROM python:3.12-slim

WORKDIR /app

# Uncomment to enable OCR ingestion of scanned reports.
# RUN apt-get update && apt-get install -y --no-install-recommends \
#         tesseract-ocr && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY data/synthetic/ /app/data/synthetic/

ENV PYTHONPATH=/app/backend \
    NWIS_DATA_DIR=/app/data/synthetic \
    NWIS_DB=/app/data/nwis.db \
    NWIS_FRONTEND=/app/frontend/dist

EXPOSE 8000

# The app builds the knowledge base on first start if it is missing.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

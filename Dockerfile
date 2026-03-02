FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATA_DIR=/app/data
RUN mkdir -p /app/data && python seed.py

EXPOSE 7002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7002"]

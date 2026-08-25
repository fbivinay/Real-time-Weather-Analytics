FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY weather_generator.py kafka_producer.py .

CMD ["python", "kafka_producer.py"]

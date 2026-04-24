FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir paho-mqtt==1.6.1

COPY remeha_mqtt.py .

CMD ["python", "-u", "remeha_mqtt.py"]

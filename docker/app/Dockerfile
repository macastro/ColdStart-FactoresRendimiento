FROM python:3.11-slim

WORKDIR /app
COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1
ENV MODE=cpu

CMD ["python", "/app/app.py"]

FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask google-auth requests

COPY web.py /app/web.py

EXPOSE 8080

CMD ["python", "/app/web.py"]

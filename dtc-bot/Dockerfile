FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Default: 24/7 paper trading. Override with e.g.:
#   docker run dtc-bot python run.py backtest --days 180
#   docker run dtc-bot python run.py live --i-understand-live
CMD ["python", "run.py", "paper", "--config", "config.yaml"]

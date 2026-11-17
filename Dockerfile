FROM python:3.12-slim
# Hugging Face Spaces runs containers as uid 1000, so create that user and make the app folder (and data/) writable by it.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY --chown=user . .
USER user
ENV FOODFLOW_DB=/home/user/app/data/foodflow.db PORT=7860
EXPOSE 7860
# Hugging Face uses 7860; Render / Railway / Fly inject their own $PORT.
CMD ["sh", "-c", "gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-7860} app:app"]

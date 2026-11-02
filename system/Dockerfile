FROM python:3.11-slim

# Mencegah penulisan file .pyc dan memastikan log langsung tampil
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Layer caching: install requirements terlebih dahulu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy kode aplikasi dan dataset
COPY . .

EXPOSE 8000

# Bind host ke 0.0.0.0 agar dapat diakses dari luar container
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

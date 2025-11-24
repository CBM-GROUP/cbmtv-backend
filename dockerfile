# Use official Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=cbmtv.settings.production

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project
COPY . /app/

# Create static directory
RUN mkdir -p /app/staticfiles

# Expose port
EXPOSE 8000

# Run collectstatic, migrations, and start Gunicorn at runtime
CMD python manage.py collectstatic --noinput && \
    python manage.py migrate && \
    gunicorn cbmtv.wsgi:application --bind 0.0.0.0:8000

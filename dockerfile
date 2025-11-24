# Use official Python image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

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

# Collect static files (optional)
RUN python manage.py collectstatic --noinput

# Run migrations (optional)
# RUN python manage.py migrate

# Expose port
EXPOSE 8000

# Command to run the app
CMD ["gunicorn", "cbmtv.wsgi:application", "--bind", "0.0.0.0:8000"]

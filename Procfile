web: python manage.py migrate && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --worker-class gthread --workers 1 --threads 4 --timeout 60

# Brightloop Subscription Management System

Django + PostgreSQL + HTMX/Alpine/Tailwind. Modular monolith (`apps/<module>/{models,services,selectors,views}`).

## Run locally (Windows)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env      # then set DB_PASSWORD
python manage.py runserver
```

Create the database first: `createdb -U postgres brightloop` (or via pgAdmin).

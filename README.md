# PTIT Lab Progress (Flask)

A Flask application for managing lab progress and commitments using Flask + SQLite.

## Prerequisites

- Python 3.10+
- Windows/Linux/Mac

## Installation

1. Clone the repository
2. Create virtual environment:
   ```
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

## Running the Application

The database and default admin user will be created automatically on first run.

Set environment variables and run:

```
set FLASK_APP=app.py
set FLASK_ENV=development
flask run --host=0.0.0.0 --port=5000
```

Or simply:

```
python app.py
```

Access at http://127.0.0.1:5000/

## Default Credentials

- Admin: admin / admin123

## Configuration

- `config.py`: Contains SECRET_KEY, SQLALCHEMY_DATABASE_URI (SQLite file: instance/ptit_lab_progress.db), UPLOAD_FOLDER (uploads/)

## Project Structure

- `app.py`: Main application file
- `models.py`: Database models
- `config.py`: Configuration
- `templates/`: HTML templates
- `static/`: CSS, JS files
- `instance/`: Database file
- `uploads/`: Uploaded files
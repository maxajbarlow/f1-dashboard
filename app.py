#!/usr/bin/env python3
"""
F1 Weekend Countdown Dashboard
Displays countdowns to upcoming F1 sessions across multiple race weekends
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, flash, session
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from werkzeug.utils import secure_filename
import tempfile
import re
from typing import Dict, List, Optional, Any
import logging
import pytz
from functools import wraps
from flask_dance.contrib.github import make_github_blueprint, github

# For PDF processing
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Generate a secure random secret key - in production, set via environment variable
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# GitHub OAuth configuration
GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
ALLOWED_GITHUB_USERS = os.environ.get('ALLOWED_GITHUB_USERS', '').split(',')  # Comma-separated usernames

# Only enable GitHub OAuth if credentials are provided
if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
    github_bp = make_github_blueprint(
        client_id=GITHUB_CLIENT_ID,
        client_secret=GITHUB_CLIENT_SECRET,
        scope='user:email'
    )
    app.register_blueprint(github_bp, url_prefix='/login')
    GITHUB_AUTH_ENABLED = True
else:
    GITHUB_AUTH_ENABLED = False
    logger.warning("GitHub OAuth not configured - admin endpoints unprotected!")

def require_github_auth(f):
    """Decorator to require GitHub authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not GITHUB_AUTH_ENABLED:
            # If GitHub auth is not configured, allow access (backward compatibility)
            return f(*args, **kwargs)

        if not github.authorized:
            return redirect(url_for('github.login'))

        # Check if user is in allowed list
        if ALLOWED_GITHUB_USERS and ALLOWED_GITHUB_USERS[0]:  # Check if list is not empty
            try:
                resp = github.get('/user')
                if resp.ok:
                    username = resp.json()['login']
                    if username not in ALLOWED_GITHUB_USERS:
                        flash(f'Access denied. User {username} not authorized.', 'error')
                        return redirect(url_for('index'))
            except Exception as e:
                logger.error(f"GitHub auth error: {e}")
                return redirect(url_for('github.login'))

        return f(*args, **kwargs)
    return decorated_function

# Security headers
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    return response

ALLOWED_EXTENSIONS = {'json', 'csv', 'txt', 'html', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

DATA_DIR = Path(__file__).parent / 'data'


class F1TimetableRawExtractor:
    """
    Raw extractor - captures ALL timetable data in clean JSON format
    No filtering, no categorization - just structured extraction
    """

    def __init__(self, pdf_path: str):
        self.pdf_path = Path(pdf_path)
        self.data = {
            'event_name': '',
            'location': '',
            'year': '',
            'version': '',
            'days': []
        }

    def extract(self) -> Dict[str, Any]:
        """
        Main extraction method - captures everything
        """
        if not pdfplumber:
            raise ImportError("pdfplumber is required for PDF processing. Please install it with: pip install pdfplumber")

        try:
            logger.info(f"Processing PDF: {self.pdf_path}")

            with pdfplumber.open(self.pdf_path) as pdf:
                # Extract metadata from first page
                self._extract_metadata(pdf.pages[0])

                # Process each page
                for page_num, page in enumerate(pdf.pages, 1):
                    logger.info(f"Processing page {page_num}/{len(pdf.pages)}")
                    self._extract_page_data(page, page_num)

            logger.info(f"Extraction complete: {len(self.data['days'])} days extracted")
            return self.data

        except Exception as e:
            logger.error(f"Extraction failed: {e}", exc_info=True)
            raise

    def _extract_metadata(self, first_page) -> None:
        """
        Extract event metadata from the first page
        """
        try:
            text = first_page.extract_text()
            all_text = '\n'.join(text.split('\n'))

            # Extract event name
            match = re.search(r'FORMULA\s*1\s+([A-Z\s]+?)\s*GRAND\s*PRIX', all_text, re.IGNORECASE)
            if match:
                event_middle = match.group(1).strip()
                event_middle = re.sub(r'([a-z])([A-Z])', r'\1 \2', event_middle)
                self.data['event_name'] = f"FORMULA 1 {event_middle} GRAND PRIX"

            # Extract location
            match = re.search(r'(Marina\s*Bay|Circuit[^,\n]*)', all_text, re.IGNORECASE)
            if match:
                self.data['location'] = re.sub(r'\s+', ' ', match.group(0).strip())

            # Extract version
            match = re.search(r'Version\s*(\d+)', all_text, re.IGNORECASE)
            if match:
                self.data['version'] = match.group(1)

            # Extract year
            year_match = re.search(r'20\d{2}', all_text)
            if year_match:
                self.data['year'] = year_match.group(0)

            logger.info(f"Metadata: {self.data['event_name']} {self.data['year']}")

        except Exception as e:
            logger.warning(f"Metadata extraction partial failure: {e}")

    def _extract_page_data(self, page, page_num: int) -> None:
        """
        Extract all data from a page - no filtering
        """
        try:
            # Extract tables
            tables = page.extract_tables()

            if not tables:
                logger.warning(f"No tables found on page {page_num}")
                return

            # Get main table
            main_table = max(tables, key=lambda t: len(t) if t else 0)

            if not main_table or len(main_table) < 2:
                logger.warning(f"Invalid table structure on page {page_num}")
                return

            # Extract day info
            day_info = self._extract_day_info(page)
            if not day_info:
                logger.warning(f"Could not extract day info from page {page_num}")
                return

            # Create day object
            day_object = {
                'day_name': day_info['day_name'],
                'date': day_info['date'],
                'events': []
            }

            # Process ALL rows - no filtering
            for row in main_table[1:]:  # Skip header
                if not row or len(row) < 2:
                    continue

                event = self._parse_table_row(row)
                if event:
                    day_object['events'].append(event)

            self.data['days'].append(day_object)
            logger.info(f"Extracted {len(day_object['events'])} events from {day_info['date']}")

        except Exception as e:
            logger.error(f"Error extracting page {page_num}: {e}", exc_info=True)

    def _extract_day_info(self, page) -> Optional[Dict[str, str]]:
        """
        Extract day and date information
        """
        try:
            text = page.extract_text()
            lines = text.split('\n')

            for line in lines[:15]:
                match = re.search(
                    r'(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\s*(\d{1,2})\s*(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s*(20\d{2})',
                    line
                )
                if match:
                    day_name = match.group(1).title()
                    day = match.group(2).zfill(2)
                    month = match.group(3)
                    year = match.group(4)

                    date_str = f"{year}-{self._month_to_number(month)}-{day}"

                    return {
                        'day_name': day_name,
                        'date': date_str
                    }

            return None

        except Exception as e:
            logger.error(f"Error extracting day info: {e}")
            return None

    def _parse_table_row(self, row: List[str]) -> Optional[Dict[str, Any]]:
        """
        Parse a table row - extract ALL fields cleanly
        """
        try:
            # Clean cells
            row = [cell.strip() if cell else '' for cell in row]

            # Skip completely empty rows
            if not any(row):
                return None

            # Extract times
            start_time = ''
            end_time = ''
            time_pattern = r'(\d{1,2}:\d{2})'

            # Check first 3 cells for times
            for i, cell in enumerate(row[:3]):
                times = re.findall(time_pattern, cell)
                if times:
                    if not start_time:
                        start_time = times[0]
                    elif not end_time and times[0] != start_time:
                        end_time = times[0]

            # Build event object with ALL data
            event = {
                'start_time': start_time,
                'end_time': end_time,
                'category': '',
                'location': '',
                'description': ''
            }

            # Extract category, location, and description from remaining cells
            for i, cell in enumerate(row):
                cell_upper = cell.upper()

                # Skip time columns
                if i < 2:
                    continue

                # Try to identify category (index 2)
                if i == 2 and cell:
                    if any(keyword in cell_upper.replace(' ', '') for keyword in
                          ['FORMULA1', 'F1ACADEMY', 'PORSCHE', 'FIA', 'PROMOTER', 'PADDOCK',
                           'F1EXPERIENCES', 'STEMRACING']):
                        event['category'] = self._normalize_text(cell)
                        continue

                # Try to identify location
                if any(keyword in cell_upper.replace(' ', '') for keyword in
                      ['TRACK', 'PITLANE', 'PRESSCONFERENCEROOM', 'ONLINEMEETING']):
                    event['location'] = self._normalize_text(cell)
                    continue

                # Everything else is description
                if cell and not event['description']:
                    event['description'] = self._normalize_text(cell)
                elif cell:
                    # Append if we have multiple description cells
                    event['description'] = f"{event['description']} - {self._normalize_text(cell)}"

            # Return event only if we have at least a time or description
            if start_time or event['description']:
                return event

            return None

        except Exception as e:
            logger.warning(f"Error parsing row: {e}")
            return None

    def _normalize_text(self, text: str) -> str:
        """
        Normalize text by adding spaces where needed
        Handles all compound words and spacing issues
        """
        if not text:
            return text

        # Comprehensive replacements - do these FIRST before any regex
        replacements = {
            # Core F1 terms
            'F1ACADEMY': 'F1 ACADEMY',
            'F1EXPERIENCES': 'F1 EXPERIENCES',
            'FORMULA1': 'FORMULA 1',
            'F1STEWARDS': 'F1 STEWARDS',
            'F1CAR': 'F1 CAR',
            'F1DRIVERS': 'F1 DRIVERS',
            'F1PASS': 'F1 PASS',
            'F1SYSTEMS': 'F1 SYSTEMS',
            'TOOF1': 'TO F1',
            'OPENTOF1': 'OPEN TO F1',

            # Location compounds
            'PITLANE': 'PIT LANE',
            'PITLANEWALK': 'PIT LANE WALK',
            'LANEWALK': 'LANE WALK',
            'PITLANEOPEN': 'PIT LANE OPEN',
            'LANEOPEN': 'LANE OPEN',
            'PRESSCONFERENCEROOM': 'PRESS CONFERENCE ROOM',
            'PRESSCONFERENCE': 'PRESS CONFERENCE',
            'ONLINEMEETING': 'ONLINE MEETING',

            # Track related
            'TRACKCLOSED': 'TRACK CLOSED',
            'TRACKOPEN': 'TRACK OPEN',
            'TRACKINSPECTION': 'TRACK INSPECTION',
            'TRACKACCESS': 'TRACK ACCESS',
            'TRACKTEST': 'TRACK TEST',
            'TRACKCOMPLETELYCLEAR': 'TRACK COMPLETELY CLEAR',

            # Safety/Medical
            'SAFETYCAR': 'SAFETY CAR',
            'SAFETYCARTEST': 'SAFETY CAR TEST',
            'CARTEST': 'CAR TEST',
            'MEDICALCAR': 'MEDICAL CAR',
            'MEDICALCARS': 'MEDICAL CARS',
            'MEDICALINSPECTION': 'MEDICAL INSPECTION',
            'MEDICALINTERVENTION': 'MEDICAL INTERVENTION',
            'INTERVENTIONEXERCISE': 'INTERVENTION EXERCISE',
            'HIGHSPEEDTRACKTEST': 'HIGH SPEED TRACK TEST',
            'HIGHSPEEDTRACK': 'HIGH SPEED TRACK',
            'FIASAFETY': 'FIA SAFETY',

            # Curfew
            'TEAMCURFEW': 'TEAM CURFEW',
            'CURFEWENDS': 'CURFEW ENDS',
            'CURFEWSTARTS': 'CURFEW STARTS',

            # Session types
            'PRACTICESESSION': 'PRACTICE SESSION',
            'QUALIFYINGSESSION': 'QUALIFYING SESSION',
            'FIRSTPRACTICE': 'FIRST PRACTICE',
            'SECONDPRACTICE': 'SECOND PRACTICE',
            'THIRDPRACTICE': 'THIRD PRACTICE',
            'GRANDPRIX': 'GRAND PRIX',
            'GRIDPROCEDURE': 'GRID PROCEDURE',

            # Race specifics
            'FIRSTRACE': 'FIRST RACE',
            'SECONDRACE': 'SECOND RACE',
            'LAPSOR': 'LAPS OR',
            'LAPS,MAX': 'LAPS, MAX',
            'MAX30MINS': 'MAX 30 MINS',
            '30MINS': '30 MINS',
            '120MINUTES': '120 MINUTES',
            '12LAPS': '12 LAPS',
            '14LAPS': '14 LAPS',
            '62LAPS': '62 LAPS',

            # Facilities/Events
            'PASSHOLDERS': 'PASS HOLDERS',
            'PADDOCKCLUB': 'PADDOCK CLUB',
            'CLUBPIT': 'CLUB PIT',
            'COMMUNITYPIT': 'COMMUNITY PIT',
            'TEAMMANAGERS': 'TEAM MANAGERS',
            'TEAMSPRESS': 'TEAMS PRESS',
            'PROMOTERACTIVITY': 'PROMOTER ACTIVITY',
            'STEMRACING': 'STEM RACING',
            'PORSCHECARRERACUP': 'PORSCHE CARRERA CUP',
            'NATIONALANTHEM': 'NATIONAL ANTHEM',
            'MARSHALLS': 'MARSHALLS',
            'SECURITYBRIEFING': 'SECURITY BRIEFING',

            # Presentation/Ceremony
            'CARPRESENTATION': 'CAR PRESENTATION',
            'CARCOVERSEALS': 'CAR COVER SEALS',
            'COVERSEALS': 'COVER SEALS',
            'SEALSREMOVED': 'SEALS REMOVED',
            'EXPERIENCESCHAMPIONSCLUB': 'EXPERIENCES CHAMPIONS CLUB',
            'CHAMPIONSCLUBTROPHY': 'CHAMPIONS CLUB TROPHY',
            'CLUBTROPHY': 'CLUB TROPHY',
            'TROPHYPHOTO': 'TROPHY PHOTO',
            'GRIDWALK': 'GRID WALK',
            'DRIVERS': 'DRIVERS',
            'FAMILIARISATION': 'FAMILIARISATION',
            'SYSTEMSCHECKS': 'SYSTEMS CHECKS',
        }

        # Apply all replacements
        normalized = text.upper()
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        # Fix specific patterns
        # Remove extra spaces around special chars and fix them
        normalized = re.sub(r'\s*([,/\-&])\s*', r' \1 ', normalized)

        # Add space between letter and opening parenthesis
        normalized = re.sub(r'([A-Z])(\()', r'\1 \2', normalized)

        # Clean up "FORFIA/F1ONLY" type patterns
        normalized = normalized.replace('FORFIA', 'FOR FIA')
        normalized = normalized.replace('F1ONLY', 'F1 ONLY')

        # Fix apostrophes - ensure space after
        normalized = re.sub(r"'([A-Z])", r"' \1", normalized)

        # Clean up multiple spaces
        normalized = re.sub(r'\s+', ' ', normalized)

        return normalized.strip()

    def _month_to_number(self, month_name: str) -> str:
        """Convert month name to number"""
        months = {
            'JANUARY': '01', 'FEBRUARY': '02', 'MARCH': '03',
            'APRIL': '04', 'MAY': '05', 'JUNE': '06',
            'JULY': '07', 'AUGUST': '08', 'SEPTEMBER': '09',
            'OCTOBER': '10', 'NOVEMBER': '11', 'DECEMBER': '12'
        }
        return months.get(month_name, '00')

    def to_json(self, output_path: Optional[str] = None, indent: int = 2) -> str:
        """
        Export to clean, well-formatted JSON
        """
        json_str = json.dumps(self.data, indent=indent, ensure_ascii=False)

        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_str)
            logger.info(f"Data exported to {output_path}")

        return json_str

def load_race_data():
    """Load all race weekend data from JSON files"""
    races = []

    # Load all JSON files from data directory
    if DATA_DIR.exists():
        for filepath in DATA_DIR.glob('*.json'):
            # Skip hidden files
            if filepath.name.startswith('.'):
                continue

            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    # Generate race name from filename or use event_name from data
                    race_name = data.get('event_name') or data.get('location') or filepath.stem.title()
                    data['race_name'] = race_name
                    races.append(data)
                    logger.info(f"Loaded race data from {filepath.name}: {race_name}")

            except Exception as e:
                logger.error(f"Error loading {filepath.name}: {e}")

    return races

def get_all_sessions(races):
    """Extract ALL sessions (F1, F2, F3, etc.) with timestamps"""
    all_sessions = []

    # Timezone mapping for race locations
    location_timezones = {
        'Marina Bay': 'Asia/Singapore',  # Singapore GP - SGT (UTC+8)
        'Monza': 'Europe/Rome',  # Italian GP - CET/CEST
        'Baku': 'Asia/Baku',  # Azerbaijan GP - AZT (UTC+4)
    }

    for race in races:
        race_name = race.get('race_name', 'Unknown')
        location = race.get('location', '')
        year = race.get('year', '')

        # Get timezone for this location, default to UTC
        timezone_name = location_timezones.get(location, 'UTC')

        for date_key, day_data in race.get('days', {}).items():
            day_name = day_data.get('day_name', '')

            # Get ALL sessions (no filtering)
            for session in day_data.get('sessions', []):
                start_time = session.get('start_time', '')
                category = session.get('category', '')

                # Parse datetime
                try:
                    # Parse as naive datetime first
                    naive_dt = datetime.strptime(
                        f"{date_key} {start_time}",
                        "%Y-%m-%d %H:%M"
                    )

                    # Localize to event timezone
                    tz = pytz.timezone(timezone_name)
                    local_dt = tz.localize(naive_dt)

                    # Convert to UTC for storage
                    utc_dt = local_dt.astimezone(pytz.UTC)

                    all_sessions.append({
                        'race': race_name,
                        'location': location,
                        'day': day_name,
                        'date': date_key,
                        'time': start_time,
                        'category': category,
                        'activity': session.get('activity', ''),
                        'datetime': utc_dt.isoformat(),
                        'local_datetime': local_dt.isoformat(),
                        'timezone': timezone_name,
                        'timestamp': utc_dt.timestamp()
                    })
                except ValueError:
                    continue

            # Also include other_events if needed
            for event in day_data.get('other_events', []):
                start_time = event.get('start_time', '')
                category = event.get('category', '')

                # Skip if no start time
                if not start_time:
                    continue

                # Parse datetime
                try:
                    # Parse as naive datetime first
                    naive_dt = datetime.strptime(
                        f"{date_key} {start_time}",
                        "%Y-%m-%d %H:%M"
                    )

                    # Localize to event timezone
                    tz = pytz.timezone(timezone_name)
                    local_dt = tz.localize(naive_dt)

                    # Convert to UTC for storage
                    utc_dt = local_dt.astimezone(pytz.UTC)

                    all_sessions.append({
                        'race': race_name,
                        'location': location,
                        'day': day_name,
                        'date': date_key,
                        'time': start_time,
                        'category': category,
                        'activity': event.get('activity', ''),
                        'datetime': utc_dt.isoformat(),
                        'local_datetime': local_dt.isoformat(),
                        'timezone': timezone_name,
                        'timestamp': utc_dt.timestamp()
                    })
                except ValueError:
                    continue

    # Filter out sessions that have already started
    now = datetime.now(pytz.UTC)
    current_timestamp = now.timestamp()

    # Only show sessions that haven't started yet (future sessions only)
    future_sessions = []
    for session in all_sessions:
        session_start = session['timestamp']
        # Only keep sessions that start in the future
        if session_start > current_timestamp:
            future_sessions.append(session)

    # Sort by datetime
    future_sessions.sort(key=lambda x: x['timestamp'])

    return future_sessions

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')

@app.route('/api/sessions')
def api_sessions():
    """API endpoint to get all sessions - no filtering"""
    races = load_race_data()
    sessions = get_all_sessions(races)

    return jsonify({
        'sessions': sessions,
        'current_time': datetime.now(pytz.UTC).isoformat()
    })

@app.route('/upload')
@require_github_auth
def upload_page():
    """File upload page"""
    flash_messages = []
    for category, message in request.args.items():
        if category in ['success', 'error']:
            flash_messages.append(f'<div class="flash {category}">{message}</div>')

    # Get actual flash messages from session
    session_flashes = []
    for category, message in flash.get_flashed_messages(with_categories=True):
        session_flashes.append(f'<div class="flash {category}">{message}</div>')

    all_flashes = ''.join(session_flashes + flash_messages)

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>F1 Data Upload</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #0f1419; color: #e6e6e6; }}
            .container {{ max-width: 600px; margin: 0 auto; }}
            .upload-box {{ border: 2px dashed #e10600; padding: 40px; text-align: center; background: #1a1f2e; border-radius: 8px; }}
            input[type="file"] {{ margin: 20px 0; }}
            button {{ background: #e10600; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; }}
            button:hover {{ background: #ff0800; }}
            .back-link {{ color: #e10600; text-decoration: none; }}
            .back-link:hover {{ text-decoration: underline; }}
            .flash {{ padding: 10px; margin: 10px 0; border-radius: 4px; }}
            .flash.success {{ background: #28a745; }}
            .flash.error {{ background: #dc3545; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🏁 Upload F1 Schedule Data</h1>
            <p><a href="/" class="back-link">← Back to Dashboard</a></p>

            {all_flashes}

            <div class="upload-box">
                <h3>Upload F1 Timetable PDF</h3>
                <p>Upload an official F1 timetable PDF to automatically extract schedule data</p>
                <form action="/upload" method="post" enctype="multipart/form-data">
                    <input type="file" name="file" accept=".pdf" required>
                    <br>
                    <button type="submit">Upload & Parse</button>
                </form>
            </div>

            <div style="margin-top: 30px;">
                <h3>Current Data Files:</h3>
                <div id="data-files">Loading...</div>
            </div>
        </div>

        <script>
            // Load current data files
            fetch('/api/data-files')
                .then(response => response.json())
                .then(data => {{
                    const container = document.getElementById('data-files');
                    if (data.files.length === 0) {{
                        container.innerHTML = '<em>No data files found</em>';
                    }} else {{
                        container.innerHTML = data.files.map(file =>
                            `<div>📄 ${{file}}</div>`
                        ).join('');
                    }}
                }});
        </script>
    </body>
    </html>
    '''
    return html

@app.route('/upload', methods=['POST'])
@require_github_auth
def upload_file():
    """Handle file upload and parsing"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(request.url)

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(request.url)

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)

        # Save to temporary file
        with tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix=f'_{filename}') as temp_file:
            file.save(temp_file.name)
            temp_path = temp_file.name

        try:
            # Here's where your parsing script will be called
            result = parse_uploaded_file(temp_path, filename)

            if result['success']:
                flash(f'Successfully parsed and saved: {result["message"]}', 'success')
            else:
                flash(f'Parsing failed: {result["error"]}', 'error')

        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'error')
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass

        return redirect(url_for('upload_page'))
    else:
        flash('Invalid file type. Please upload JSON, CSV, TXT, or HTML files.', 'error')
        return redirect(request.url)

def parse_uploaded_file(file_path, original_filename):
    """Parse uploaded file using F1 timetable extractor"""
    try:
        # Check if it's a PDF file by extension
        if not original_filename.lower().endswith('.pdf'):
            return {
                'success': False,
                'error': 'Only PDF files are supported for F1 timetable extraction. Please upload a PDF file.'
            }

        # Verify PDF magic bytes (PDF files start with %PDF-)
        with open(file_path, 'rb') as f:
            magic = f.read(5)
            if magic != b'%PDF-':
                return {
                    'success': False,
                    'error': 'File is not a valid PDF (magic byte check failed)'
                }

        # Use the F1TimetableRawExtractor
        extractor = F1TimetableRawExtractor(file_path)
        data = extractor.extract()

        if not data or not data.get('days'):
            return {
                'success': False,
                'error': 'No F1 schedule data found in the PDF. Please check the file format.'
            }

        # Generate output filename based on event
        event_name = data.get('event_name', 'f1_event')
        location = data.get('location', 'unknown')

        # Create clean filename
        safe_name = re.sub(r'[^\w\s-]', '', f"{location}_{event_name}").strip()
        safe_name = re.sub(r'[-\s]+', '_', safe_name).lower()
        output_filename = f"{safe_name}.json"

        # Save to data directory
        output_path = DATA_DIR / output_filename

        # Ensure data directory exists
        DATA_DIR.mkdir(exist_ok=True)

        # Convert to the format expected by the existing app
        converted_data = convert_extracted_data_to_app_format(data)

        # Save the converted data
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(converted_data, f, indent=2, ensure_ascii=False)

        total_events = sum(len(day['events']) for day in data['days'])
        days_count = len(data['days'])

        return {
            'success': True,
            'message': f'Successfully extracted {total_events} events across {days_count} days from {data.get("event_name", "F1 Event")}. Saved as {output_filename}',
            'data': {
                'event_name': data.get('event_name'),
                'location': data.get('location'),
                'year': data.get('year'),
                'days': days_count,
                'total_events': total_events,
                'filename': output_filename
            }
        }

    except Exception as e:
        return {
            'success': False,
            'error': f'Error parsing PDF: {str(e)}'
        }

def convert_extracted_data_to_app_format(extracted_data):
    """Convert the extracted data format to the format expected by the existing app"""
    converted = {
        'event_name': extracted_data.get('event_name', ''),
        'location': extracted_data.get('location', ''),
        'year': extracted_data.get('year', ''),
        'version': extracted_data.get('version', ''),
        'days': {}
    }

    for day in extracted_data.get('days', []):
        date = day.get('date')
        if date:
            converted['days'][date] = {
                'day_name': day.get('day_name'),
                'sessions': [],
                'other_events': []
            }

            # Convert events to sessions format
            for event in day.get('events', []):
                session = {
                    'start_time': event.get('start_time', ''),
                    'end_time': event.get('end_time', ''),
                    'category': event.get('category', ''),
                    'activity': event.get('description', ''),
                    'location': event.get('location', '')
                }

                # Add to sessions list
                converted['days'][date]['sessions'].append(session)

    return converted

@app.route('/api/data-files')
def api_data_files():
    """API endpoint to list current data files"""
    data_dir = Path(__file__).parent / 'data'
    files = []

    if data_dir.exists():
        for file_path in data_dir.glob('*.json'):
            files.append(file_path.name)

    return jsonify({'files': files})

# Configuration file path
CONFIG_FILE = Path(__file__).parent / 'data' / 'schedule_config.json'

def load_config():
    """Load schedule configuration from file"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")

    # Default configuration
    return {
        'meal_times': {
            'Tuesday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Wednesday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Thursday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Friday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Saturday': {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:30'},
            'Sunday': {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:30'}
        },
        'hotel_leave_times': {
            'Tuesday': '08:30',
            'Wednesday': '08:30',
            'Thursday': '08:30',
            'Friday': '09:00',
            'Saturday': '10:00',
            'Sunday': '11:00'
        }
    }

def save_config(config):
    """Save schedule configuration to file"""
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False

@app.route('/config')
@require_github_auth
def config_page():
    """Configuration page for meal and hotel leave times"""
    config = load_config()

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Schedule Configuration</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: #0f1419;
                color: #e6e6e6;
                padding: 20px;
                line-height: 1.6;
            }

            .container {
                max-width: 900px;
                margin: 0 auto;
            }

            header {
                text-align: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 2px solid #e10600;
            }

            h1 {
                font-size: 2rem;
                color: #e10600;
                margin-bottom: 10px;
            }

            .back-link {
                display: inline-block;
                color: #e10600;
                text-decoration: none;
                margin-bottom: 20px;
                padding: 8px 16px;
                border: 1px solid #e10600;
                border-radius: 4px;
                transition: all 0.2s ease;
            }

            .back-link:hover {
                background: #e10600;
                color: white;
            }

            .section {
                background: #1a1f2e;
                border: 1px solid #2a3040;
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 30px;
            }

            .section-title {
                font-size: 1.5rem;
                color: #e10600;
                margin-bottom: 20px;
                padding-bottom: 10px;
                border-bottom: 1px solid #2a3040;
            }

            .day-config {
                display: grid;
                grid-template-columns: 120px 1fr;
                gap: 15px;
                align-items: center;
                margin-bottom: 15px;
                padding: 15px;
                background: #252b3d;
                border-radius: 8px;
            }

            .day-name {
                font-weight: 600;
                color: #e10600;
                font-size: 1.1rem;
            }

            .time-inputs {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 12px;
            }

            .time-field {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }

            .time-field label {
                font-size: 0.85rem;
                color: #999;
                font-weight: 500;
            }

            .time-field input {
                background: #1a1f2e;
                border: 1px solid #2a3040;
                color: #e6e6e6;
                padding: 8px 12px;
                border-radius: 6px;
                font-size: 1rem;
                font-family: monospace;
                transition: all 0.2s ease;
            }

            .time-field input:focus {
                outline: none;
                border-color: #e10600;
                background: #1f2430;
            }

            .button-group {
                display: flex;
                gap: 15px;
                justify-content: center;
                margin-top: 30px;
            }

            button {
                background: #e10600;
                color: white;
                border: none;
                padding: 12px 30px;
                border-radius: 8px;
                font-size: 1rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
            }

            button:hover {
                background: #ff0800;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(225, 6, 0, 0.3);
            }

            button.secondary {
                background: #2a3040;
                color: #e6e6e6;
            }

            button.secondary:hover {
                background: #3a4050;
            }

            .flash {
                padding: 15px;
                margin-bottom: 20px;
                border-radius: 8px;
                text-align: center;
                font-weight: 500;
            }

            .flash.success {
                background: #28a745;
                color: white;
            }

            .flash.error {
                background: #dc3545;
                color: white;
            }

            @media (max-width: 768px) {
                .day-config {
                    grid-template-columns: 1fr;
                    gap: 10px;
                }

                .time-inputs {
                    grid-template-columns: 1fr;
                }

                .button-group {
                    flex-direction: column;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <a href="/" class="back-link">← Back to Dashboard</a>

            <header>
                <h1>⚙️ Schedule Configuration</h1>
                <p>Configure meal times and hotel departure times for each day</p>
            </header>

            <div id="flash-message"></div>

            <form id="configForm">
                <!-- Meal Times Section -->
                <div class="section">
                    <h2 class="section-title">🍽️ Meal Times</h2>
    '''

    # Add meal time inputs for each day
    days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    for day in days:
        meals = config['meal_times'].get(day, {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'})
        html += f'''
                    <div class="day-config">
                        <div class="day-name">{day}</div>
                        <div class="time-inputs">
                            <div class="time-field">
                                <label>🍳 Breakfast</label>
                                <input type="time" name="{day}_breakfast" value="{meals['breakfast']}" required>
                            </div>
                            <div class="time-field">
                                <label>🍽️ Lunch</label>
                                <input type="time" name="{day}_lunch" value="{meals['lunch']}" required>
                            </div>
                            <div class="time-field">
                                <label>🍷 Dinner</label>
                                <input type="time" name="{day}_dinner" value="{meals['dinner']}" required>
                            </div>
                        </div>
                    </div>
        '''

    html += '''
                </div>

                <!-- Hotel Leave Times Section -->
                <div class="section">
                    <h2 class="section-title">🏨 Hotel Departure Times</h2>
    '''

    # Add hotel leave time inputs for each day
    for day in days:
        leave_time = config['hotel_leave_times'].get(day, '08:30')
        html += f'''
                    <div class="day-config">
                        <div class="day-name">{day}</div>
                        <div class="time-inputs">
                            <div class="time-field">
                                <label>🚗 Departure Time</label>
                                <input type="time" name="{day}_leave" value="{leave_time}" required>
                            </div>
                        </div>
                    </div>
        '''

    html += '''
                </div>

                <div class="button-group">
                    <button type="submit">Save Configuration</button>
                    <button type="button" class="secondary" onclick="resetToDefaults()">Reset to Defaults</button>
                </div>
            </form>
        </div>

        <script>
            const form = document.getElementById('configForm');
            const flashMessage = document.getElementById('flash-message');

            form.addEventListener('submit', async (e) => {
                e.preventDefault();

                const formData = new FormData(form);
                const config = {
                    meal_times: {},
                    hotel_leave_times: {}
                };

                const days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

                days.forEach(day => {
                    config.meal_times[day] = {
                        breakfast: formData.get(`${day}_breakfast`),
                        lunch: formData.get(`${day}_lunch`),
                        dinner: formData.get(`${day}_dinner`)
                    };
                    config.hotel_leave_times[day] = formData.get(`${day}_leave`);
                });

                try {
                    const response = await fetch('/api/config', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(config)
                    });

                    const result = await response.json();

                    if (result.success) {
                        showFlash('Configuration saved successfully!', 'success');
                    } else {
                        showFlash('Error saving configuration: ' + result.error, 'error');
                    }
                } catch (error) {
                    showFlash('Error saving configuration: ' + error.message, 'error');
                }
            });

            function showFlash(message, type) {
                flashMessage.innerHTML = `<div class="flash ${type}">${message}</div>`;
                setTimeout(() => {
                    flashMessage.innerHTML = '';
                }, 3000);
            }

            function resetToDefaults() {
                if (confirm('Reset all times to default values?')) {
                    window.location.href = '/api/config/reset';
                }
            }
        </script>
    </body>
    </html>
    '''

    return html

def validate_config(config):
    """Validate configuration input to prevent injection attacks"""
    if not isinstance(config, dict):
        return False, "Config must be a dictionary"

    # Validate meal_times
    if 'meal_times' in config:
        if not isinstance(config['meal_times'], dict):
            return False, "meal_times must be a dictionary"

        valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for day, meals in config['meal_times'].items():
            if day not in valid_days:
                return False, f"Invalid day: {day}"
            if not isinstance(meals, dict):
                return False, f"Meals for {day} must be a dictionary"
            for meal_type, time in meals.items():
                if meal_type not in ['breakfast', 'lunch', 'dinner']:
                    return False, f"Invalid meal type: {meal_type}"
                # Validate time format HH:MM
                if not re.match(r'^\d{2}:\d{2}$', time):
                    return False, f"Invalid time format for {meal_type}: {time}"

    # Validate hotel_leave_times
    if 'hotel_leave_times' in config:
        if not isinstance(config['hotel_leave_times'], dict):
            return False, "hotel_leave_times must be a dictionary"

        valid_days = ['Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for day, time in config['hotel_leave_times'].items():
            if day not in valid_days:
                return False, f"Invalid day: {day}"
            if not re.match(r'^\d{2}:\d{2}$', time):
                return False, f"Invalid time format for {day}: {time}"

    return True, "Valid"

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """API endpoint for getting and saving configuration"""
    if request.method == 'GET':
        return jsonify(load_config())

    elif request.method == 'POST':
        # Require auth for POST
        if GITHUB_AUTH_ENABLED and not github.authorized:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        try:
            config = request.get_json()

            # Validate input
            is_valid, error_msg = validate_config(config)
            if not is_valid:
                return jsonify({'success': False, 'error': f'Invalid configuration: {error_msg}'}), 400

            if save_config(config):
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'error': 'Failed to save configuration'})
        except Exception as e:
            logger.error(f"Config API error: {e}")
            return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/config/reset')
@require_github_auth
def reset_config():
    """Reset configuration to defaults"""
    default_config = {
        'meal_times': {
            'Tuesday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Wednesday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Thursday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Friday': {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'},
            'Saturday': {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:30'},
            'Sunday': {'breakfast': '08:00', 'lunch': '13:00', 'dinner': '19:30'}
        },
        'hotel_leave_times': {
            'Tuesday': '08:30',
            'Wednesday': '08:30',
            'Thursday': '08:30',
            'Friday': '09:00',
            'Saturday': '10:00',
            'Sunday': '11:00'
        }
    }

    if save_config(default_config):
        return redirect('/config')
    else:
        return "Error resetting configuration", 500

@app.route('/git')
@require_github_auth
def git_page():
    """Git repository management page"""
    # Get git status
    import subprocess

    try:
        status = subprocess.check_output(['git', 'status', '--short'], cwd=DATA_DIR.parent, stderr=subprocess.STDOUT).decode('utf-8')
    except subprocess.CalledProcessError as e:
        status = e.output.decode('utf-8')

    try:
        branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=DATA_DIR.parent).decode('utf-8').strip()
    except:
        branch = 'unknown'

    try:
        remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], cwd=DATA_DIR.parent).decode('utf-8').strip()
    except:
        remote = 'No remote configured'

    try:
        last_commit = subprocess.check_output(['git', 'log', '-1', '--oneline'], cwd=DATA_DIR.parent).decode('utf-8').strip()
    except:
        last_commit = 'No commits yet'

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Git Management</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: #0f1419;
                color: #e6e6e6;
                padding: 20px;
                line-height: 1.6;
            }}

            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}

            header {{
                text-align: center;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 2px solid #e10600;
            }}

            h1 {{
                font-size: 2rem;
                color: #e10600;
                margin-bottom: 10px;
            }}

            .back-link {{
                display: inline-block;
                color: #e10600;
                text-decoration: none;
                margin-bottom: 20px;
                padding: 8px 16px;
                border: 1px solid #e10600;
                border-radius: 4px;
                transition: all 0.2s ease;
            }}

            .back-link:hover {{
                background: #e10600;
                color: white;
            }}

            .info-section {{
                background: #1a1f2e;
                border: 1px solid #2a3040;
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 20px;
            }}

            .info-row {{
                display: grid;
                grid-template-columns: 150px 1fr;
                gap: 15px;
                padding: 10px 0;
                border-bottom: 1px solid #2a3040;
            }}

            .info-row:last-child {{
                border-bottom: none;
            }}

            .info-label {{
                font-weight: 600;
                color: #e10600;
            }}

            .info-value {{
                color: #e6e6e6;
                font-family: monospace;
            }}

            .status-box {{
                background: #1a1f2e;
                border: 1px solid #2a3040;
                border-radius: 8px;
                padding: 15px;
                margin-bottom: 20px;
                font-family: monospace;
                white-space: pre-wrap;
                color: #e6e6e6;
                max-height: 300px;
                overflow-y: auto;
            }}

            .actions {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 20px;
            }}

            .action-btn {{
                background: #e10600;
                color: white;
                border: none;
                padding: 12px 20px;
                border-radius: 8px;
                font-size: 1rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
            }}

            .action-btn:hover {{
                background: #ff0800;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(225, 6, 0, 0.3);
            }}

            .action-btn.secondary {{
                background: #2a3040;
            }}

            .action-btn.secondary:hover {{
                background: #3a4050;
            }}

            .flash {{
                padding: 15px;
                margin-bottom: 20px;
                border-radius: 8px;
                text-align: center;
            }}

            .flash.success {{
                background: #28a745;
                color: white;
            }}

            .flash.error {{
                background: #dc3545;
                color: white;
            }}

            .commit-form {{
                background: #1a1f2e;
                border: 1px solid #2a3040;
                border-radius: 12px;
                padding: 24px;
                margin-bottom: 20px;
            }}

            .form-group {{
                margin-bottom: 15px;
            }}

            .form-group label {{
                display: block;
                color: #e10600;
                font-weight: 600;
                margin-bottom: 8px;
            }}

            .form-group input {{
                width: 100%;
                background: #0f1419;
                border: 1px solid #2a3040;
                color: #e6e6e6;
                padding: 10px;
                border-radius: 6px;
                font-size: 1rem;
            }}

            .form-group input:focus {{
                outline: none;
                border-color: #e10600;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <a href="/" class="back-link">← Back to Dashboard</a>

            <header>
                <h1>🔧 Git Repository Management</h1>
                <p>Manage version control for F1 Dashboard</p>
            </header>

            <div id="flash-message"></div>

            <div class="info-section">
                <h2 style="color: #e10600; margin-bottom: 15px;">Repository Info</h2>
                <div class="info-row">
                    <div class="info-label">Branch:</div>
                    <div class="info-value">{branch}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">Remote:</div>
                    <div class="info-value">{remote}</div>
                </div>
                <div class="info-row">
                    <div class="info-label">Last Commit:</div>
                    <div class="info-value">{last_commit}</div>
                </div>
            </div>

            <h3 style="color: #e10600; margin-bottom: 10px;">Status</h3>
            <div class="status-box">{status if status else 'Working tree clean'}</div>

            <div class="commit-form">
                <h3 style="color: #e10600; margin-bottom: 15px;">Commit Changes</h3>
                <form id="commitForm" onsubmit="return commitChanges(event)">
                    <div class="form-group">
                        <label>Commit Message</label>
                        <input type="text" id="commitMessage" placeholder="Update F1 schedule data" required>
                    </div>
                    <button type="submit" class="action-btn">Commit & Push</button>
                </form>
            </div>

            <div class="actions">
                <button class="action-btn secondary" onclick="gitPull()">Pull Latest</button>
                <button class="action-btn secondary" onclick="gitStatus()">Refresh Status</button>
                <button class="action-btn secondary" onclick="gitLog()">View Log</button>
            </div>

            <div id="output" class="status-box" style="display: none;"></div>
        </div>

        <script>
            function showFlash(message, type) {{
                const flash = document.getElementById('flash-message');
                flash.innerHTML = `<div class="flash ${{type}}">${{message}}</div>`;
                setTimeout(() => flash.innerHTML = '', 5000);
            }}

            async function commitChanges(e) {{
                e.preventDefault();
                const message = document.getElementById('commitMessage').value;

                try {{
                    const response = await fetch('/git/commit', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{ message: message }})
                    }});
                    const result = await response.json();

                    if (result.success) {{
                        showFlash('Changes committed and pushed successfully!', 'success');
                        setTimeout(() => window.location.reload(), 1500);
                    }} else {{
                        showFlash('Error: ' + result.error, 'error');
                    }}
                }} catch (error) {{
                    showFlash('Error: ' + error.message, 'error');
                }}
            }}

            async function gitPull() {{
                try {{
                    const response = await fetch('/git/pull', {{ method: 'POST' }});
                    const result = await response.json();

                    if (result.success) {{
                        showFlash('Pulled latest changes!', 'success');
                        setTimeout(() => window.location.reload(), 1500);
                    }} else {{
                        showFlash('Error: ' + result.error, 'error');
                    }}
                }} catch (error) {{
                    showFlash('Error: ' + error.message, 'error');
                }}
            }}

            async function gitStatus() {{
                window.location.reload();
            }}

            async function gitLog() {{
                try {{
                    const response = await fetch('/git/log');
                    const result = await response.json();
                    const output = document.getElementById('output');
                    output.style.display = 'block';
                    output.textContent = result.log || 'No commits';
                }} catch (error) {{
                    showFlash('Error: ' + error.message, 'error');
                }}
            }}
        </script>
    </body>
    </html>
    '''

    return html

@app.route('/git/commit', methods=['POST'])
@require_github_auth
def git_commit():
    """Commit and push changes"""
    import subprocess

    try:
        data = request.get_json()
        message = data.get('message', 'Update from dashboard')

        repo_dir = DATA_DIR.parent

        # Add all changes
        subprocess.run(['git', 'add', '.'], cwd=repo_dir, check=True)

        # Commit
        result = subprocess.run(
            ['git', 'commit', '-m', message],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )

        if result.returncode != 0 and 'nothing to commit' in result.stdout:
            return jsonify({{'success': False, 'error': 'Nothing to commit'}})

        # Push
        subprocess.run(['git', 'push'], cwd=repo_dir, check=True, capture_output=True)

        return jsonify({{'success': True}})
    except subprocess.CalledProcessError as e:
        return jsonify({{'success': False, 'error': str(e)}})
    except Exception as e:
        return jsonify({{'success': False, 'error': str(e)}})

@app.route('/git/pull', methods=['POST'])
@require_github_auth
def git_pull():
    """Pull latest changes"""
    import subprocess

    try:
        result = subprocess.run(
            ['git', 'pull'],
            cwd=DATA_DIR.parent,
            capture_output=True,
            text=True,
            check=True
        )
        return jsonify({{'success': True, 'output': result.stdout}})
    except subprocess.CalledProcessError as e:
        return jsonify({{'success': False, 'error': e.stderr or str(e)}})

@app.route('/git/log')
@require_github_auth
def git_log():
    """Get git log"""
    import subprocess

    try:
        result = subprocess.run(
            ['git', 'log', '--oneline', '-n', '20'],
            cwd=DATA_DIR.parent,
            capture_output=True,
            text=True
        )
        return jsonify({{'log': result.stdout}})
    except Exception as e:
        return jsonify({{'log': str(e)}})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

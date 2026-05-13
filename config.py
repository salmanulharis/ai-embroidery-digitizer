"""
Configuration module for AI Embroidery Digitizer
Handles all application settings and constants
"""

import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).resolve().parent

# Flask Configuration
DEBUG = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
TESTING = os.getenv('FLASK_TESTING', 'False').lower() == 'true'
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
JSONIFY_PRETTYPRINT_REGULAR = True
JSON_SORT_KEYS = False

# Server Configuration
HOST = os.getenv('FLASK_HOST', '0.0.0.0')
PORT = int(os.getenv('FLASK_PORT', 5000))
THREADED = True

# Upload Configuration
UPLOAD_FOLDER = BASE_DIR / 'static' / 'uploads'
MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file size
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
ALLOWED_MIMETYPES = {'image/jpeg', 'image/png', 'image/webp'}

# Ensure upload folder exists
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

# Output Configuration
OUTPUT_FOLDER = BASE_DIR / 'static' / 'outputs'
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

# Image Processing Configuration
DEFAULT_IMAGE_SIZE_MM = {'width': 100, 'height': 100}
MIN_IMAGE_SIZE_MM = {'width': 10, 'height': 10}
MAX_IMAGE_SIZE_MM = {'width': 500, 'height': 500}
DPI = 96  # Screen DPI for pixel to mm conversion

# Color Reduction Configuration
DEFAULT_NUM_COLORS = 8
MIN_NUM_COLORS = 1
MAX_NUM_COLORS = 24
COLOR_QUANTIZATION_METHOD = 'kmeans'  # 'kmeans' or 'median_cut'
DITHERING_ENABLED = False
LAB_COLOR_DISTANCE_WEIGHT = 1.0
COLOR_MERGE_THRESHOLD = 15  # LAB color distance threshold for merging

# Gradient Processing Configuration
GRADIENT_DETECTION_ENABLED = True
GRADIENT_REGION_SIZE = 50  # pixels
SIMILAR_COLOR_THRESHOLD = 20  # LAB distance
MIN_REGION_SIZE = 50  # minimum pixels for a region to keep

# Image Cleanup Configuration
NOISE_REDUCTION_STRENGTH = 5  # Morphological kernel size
EDGE_SMOOTHING_SIGMA = 1.0
DILATION_ITERATIONS = 1
ERASION_ITERATIONS = 1
REMOVE_SMALL_OBJECTS = True
SMALL_OBJECT_SIZE_THRESHOLD = 100  # pixels

# Vectorization Configuration
CONTOUR_APPROXIMATION = 0.01  # Contour approximation accuracy (0-1)
MIN_CONTOUR_AREA = 50  # Minimum contour area in pixels
SMOOTH_CONTOURS = True
CONTOUR_SMOOTHING_ITERATIONS = 3

# Stitch Configuration (Machine-friendly defaults)
DEFAULT_STITCH_DENSITY = 10  # stitches per mm
MIN_STITCH_LENGTH_MM = 0.5
MAX_STITCH_LENGTH_MM = 5.0
JUMP_STITCH_THRESHOLD_MM = 10.0
SATIN_WIDTH_THRESHOLD = 5  # mm - thin regions become satin
UNDERLAY_ENABLED = True
UNDERLAY_DENSITY = 5  # stitches per mm
AUTO_TRIM_ENABLED = True
EDGE_RUN_ENABLED = True
EDGE_RUN_WIDTH = 0.5  # mm
PULL_COMPENSATION = 0.0  # mm - fabric pull adjustment

# Fill Pattern Configuration
TATAMI_FILL_ANGLE = 45  # degrees
TATAMI_FILL_LINE_SPACING = 2.0  # mm
FILL_STITCH_ANGLE = 45  # degrees
FILL_STITCH_SPACING = 1.5  # mm

# Stitch Path Optimization
MIN_REGION_FOR_FILL = 100  # pixels - regions smaller than this get satin stitch
REGION_MERGE_THRESHOLD = 20  # pixels - merge nearby regions
PATH_OPTIMIZATION_LEVEL = 'high'  # 'low', 'medium', 'high'
REMOVE_STITCH_ISLANDS = True
ISLAND_SIZE_THRESHOLD = 50  # pixels

# Embroidery Machine Configuration
DEFAULT_HOOP_SIZE = '100x100'  # mm x mm
HOOP_PRESETS = {
    'small': {'width': 100, 'height': 100},
    'medium': {'width': 150, 'height': 150},
    'large': {'width': 200, 'height': 200},
    'xl': {'width': 300, 'height': 300},
    'cap': {'width': 50, 'height': 80},
    'cuff': {'width': 50, 'height': 100},
}
MAX_STITCHES_WARNING = 100000  # Warn user if exceeds this
DEFAULT_THREAD_COLORS = [
    {'name': 'Black', 'rgb': (0, 0, 0), 'hex': '#000000'},
    {'name': 'White', 'rgb': (255, 255, 255), 'hex': '#FFFFFF'},
    {'name': 'Red', 'rgb': (255, 0, 0), 'hex': '#FF0000'},
    {'name': 'Blue', 'rgb': (0, 0, 255), 'hex': '#0000FF'},
    {'name': 'Green', 'rgb': (0, 128, 0), 'hex': '#008000'},
    {'name': 'Yellow', 'rgb': (255, 255, 0), 'hex': '#FFFF00'},
    {'name': 'Orange', 'rgb': (255, 165, 0), 'hex': '#FFA500'},
    {'name': 'Purple', 'rgb': (128, 0, 128), 'hex': '#800080'},
]

# Background Removal Configuration
BACKGROUND_REMOVAL_ENABLED = True
BACKGROUND_REMOVAL_METHOD = 'rembg'  # 'rembg' or 'color_detection'

# Progress Tracking
PROGRESS_UPDATE_INTERVAL = 0.5  # seconds
PROCESS_TIMEOUT = 300  # 5 minutes

# Celery Configuration (Optional - for background tasks)
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes

# Rate Limiting
RATE_LIMIT_ENABLED = True
RATE_LIMIT_REQUESTS = 100  # requests per time window
RATE_LIMIT_PERIOD = 3600  # seconds (1 hour)

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# CORS Configuration
CORS_ORIGINS = os.getenv('CORS_ORIGINS', '*').split(',')
CORS_ALLOW_HEADERS = ['Content-Type', 'Authorization']
CORS_METHODS = ['GET', 'POST', 'OPTIONS']

# Thread Configuration
NUM_WORKER_THREADS = int(os.getenv('NUM_WORKER_THREADS', 4))

# Environment
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')  # 'development', 'production'
IS_PRODUCTION = ENVIRONMENT == 'production'

# Performance Tuning
IMAGE_CACHE_ENABLED = True
CACHE_EXPIRATION_SECONDS = 3600  # 1 hour
MULTI_THREADING_ENABLED = True

# DST Export Configuration
DST_JUMP_STITCH_CODE = 0x80
DST_TRIM_STITCH_CODE = 0x81
DST_END_CODE = 0x8A
DST_MIN_X = -32000
DST_MAX_X = 32000
DST_MIN_Y = -32000
DST_MAX_Y = 32000

print(f"Configuration loaded - Environment: {ENVIRONMENT}, Debug: {DEBUG}")

"""
Constants and lookup tables for embroidery digitization
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Tuple


class ProcessingStage(Enum):
    """Processing pipeline stages"""
    UPLOADING = "Uploading"
    VALIDATING = "Validating image"
    CLEANING = "Cleaning image"
    BACKGROUND_REMOVAL = "Removing background"
    COLOR_DETECTION = "Detecting colors"
    GRADIENT_FLATTENING = "Flattening gradients"
    COLOR_REDUCTION = "Reducing colors"
    VECTORIZING = "Vectorizing contours"
    REGION_MERGING = "Merging regions"
    STITCH_GENERATION = "Generating stitch paths"
    PATH_OPTIMIZATION = "Optimizing paths"
    TRIM_OPTIMIZATION = "Optimizing trims"
    DST_EXPORT = "Exporting DST file"
    COMPLETE = "Complete"


class ColorQuantizationMethod(Enum):
    """Color quantization algorithms"""
    KMEANS = "kmeans"
    MEDIAN_CUT = "median_cut"
    OCTREE = "octree"


class FillType(Enum):
    """Stitch fill types"""
    SATIN = "satin"
    TATAMI = "tatami"
    AUTO = "auto"


class StitchType(Enum):
    """Stitch types in embroidery"""
    NORMAL = 0
    JUMP = 1
    TRIM = 2
    END = 3


@dataclass
class ThreadColor:
    """Thread color representation"""
    name: str
    rgb: Tuple[int, int, int]
    hex: str
    lab: Tuple[float, float, float] = None


# Standard embroidery thread colors (Simplified Palette)
STANDARD_THREAD_PALETTE = [
    ThreadColor("Black", (0, 0, 0), "#000000"),
    ThreadColor("White", (255, 255, 255), "#FFFFFF"),
    ThreadColor("Red", (255, 0, 0), "#FF0000"),
    ThreadColor("Dark Red", (128, 0, 0), "#800000"),
    ThreadColor("Blue", (0, 0, 255), "#0000FF"),
    ThreadColor("Dark Blue", (0, 0, 128), "#000080"),
    ThreadColor("Light Blue", (173, 216, 230), "#ADD8E6"),
    ThreadColor("Green", (0, 128, 0), "#008000"),
    ThreadColor("Light Green", (144, 238, 144), "#90EE90"),
    ThreadColor("Dark Green", (0, 100, 0), "#006400"),
    ThreadColor("Yellow", (255, 255, 0), "#FFFF00"),
    ThreadColor("Gold", (255, 215, 0), "#FFD700"),
    ThreadColor("Orange", (255, 165, 0), "#FFA500"),
    ThreadColor("Purple", (128, 0, 128), "#800080"),
    ThreadColor("Pink", (255, 192, 203), "#FFC0CB"),
    ThreadColor("Brown", (139, 69, 19), "#8B4513"),
    ThreadColor("Gray", (128, 128, 128), "#808080"),
    ThreadColor("Light Gray", (211, 211, 211), "#D3D3D3"),
    ThreadColor("Cyan", (0, 255, 255), "#00FFFF"),
    ThreadColor("Magenta", (255, 0, 255), "#FF00FF"),
]

# Hoop size presets
HOOP_PRESETS = {
    'small_square': {'width': 100, 'height': 100, 'name': 'Small Square (100x100mm)'},
    'medium_square': {'width': 150, 'height': 150, 'name': 'Medium Square (150x150mm)'},
    'large_square': {'width': 200, 'height': 200, 'name': 'Large Square (200x200mm)'},
    'xl_square': {'width': 300, 'height': 300, 'name': 'XL Square (300x300mm)'},
    'cap_hoop': {'width': 50, 'height': 80, 'name': 'Cap Hoop (50x80mm)'},
    'cuff_hoop': {'width': 50, 'height': 120, 'name': 'Cuff Hoop (50x120mm)'},
    'rectangle': {'width': 180, 'height': 100, 'name': 'Rectangle (180x100mm)'},
}

# Error messages
ERROR_MESSAGES = {
    'invalid_file_type': 'Invalid file type. Allowed types: JPG, JPEG, PNG, WEBP',
    'file_too_large': 'File size exceeds maximum allowed size (50MB)',
    'file_too_small': 'Image is too small (minimum 10x10 pixels)',
    'processing_timeout': 'Processing took too long. Please try with a smaller or simpler image',
    'invalid_dimensions': 'Invalid output dimensions',
    'invalid_color_count': 'Number of colors must be between 1 and 24',
    'memory_error': 'Out of memory. Please try with a smaller image',
    'processing_failed': 'Image processing failed. Please check your image and try again',
    'dst_generation_failed': 'Failed to generate DST file',
    'no_file_provided': 'No file was provided',
    'server_error': 'Internal server error. Please try again later',
}

# Success messages
SUCCESS_MESSAGES = {
    'upload_successful': 'Image uploaded successfully',
    'processing_complete': 'Image processing complete',
    'export_successful': 'DST file generated successfully',
}

# DST format constants
DST_HEADER_SIZE = 512
DST_STITCH_SIZE = 3
DST_JUMP_STITCH = 0x80
DST_TRIM_STITCH = 0x81
DST_END_STITCH = 0x8A
DST_COLOR_CHANGE = 0xC0

# Pixel to millimeter conversion (assumes 96 DPI)
PIXEL_TO_MM = 0.2645833  # 1 pixel at 96 DPI = 0.2645833 mm
MM_TO_PIXEL = 3.7795275  # 1 mm = 3.7795275 pixels at 96 DPI

# Stitch limits for safety
STITCH_COUNT_WARNING = 100000
STITCH_COUNT_ERROR = 500000

# Color space constants
RGB_TO_LAB_CONVERSION = "RGB to LAB conversion matrix"
